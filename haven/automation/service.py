"""Durable, feed-driven service for resource-action automations.

The service is the composition boundary that the standalone
``ResourceActionScheduler`` intentionally does not own. It restores rules and
operational dedup state, durably queues publisher-stamped events before
dispatch, consumes them in a daemon worker, and routes actions through the
existing governed domain service supplied by the composition root.
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from ..core.domain import DecisionStatus, Principal, RuleStatus
from .events import AutomationEvent, AutomationEventFeed
from .deadlines import AutomationDeadline
from .lifecycle import (
    AutomationLifecycleEvent,
    AutomationRule,
    RuleTransitionResult,
    approve,
    propose,
    revoke,
    set_enabled,
)
from .persistence import ResourceAutomationSnapshot, ResourceAutomationStore
from .resource_scheduler import ResourceActionScheduler, ResourceScheduleOutcome, ResourceScheduleStatus
from .schema import AutomationSpec

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


class ResourceAutomationService:
    """Persistent resource-automation lifecycle plus event consumption."""

    def __init__(
        self,
        *,
        path: str | Path,
        household_id: str,
        feed: AutomationEventFeed,
        dispatch: Mapping[str, Callable[..., dict]],
        deadline_provider: Callable[[], Iterable[AutomationDeadline]] | None = None,
        clock=_DEFAULT_CLOCK,
        tick_interval: float = 20.0,
    ) -> None:
        if not isinstance(household_id, str) or not household_id.strip():
            raise ValueError("household_id must be a non-empty string")
        if feed.household_id != household_id:
            raise ValueError("resource automation feed household does not match service household")
        self._household_id = household_id.strip()
        if isinstance(tick_interval, bool) or not isinstance(tick_interval, (int, float)) or tick_interval <= 0:
            raise ValueError("tick_interval must be a positive number")
        self._clock = clock
        self._tick_interval = float(tick_interval)
        self._feed = feed
        self._deadline_provider = deadline_provider
        self._store = ResourceAutomationStore(path)
        loaded = self._store.load()
        self._lock = threading.RLock()
        self._rules: dict[str, AutomationRule] = {
            rule.rule_id: rule
            for rule in loaded.rules
            if rule.spec.household_id == self._household_id
        }
        self._lifecycle_events = [
            event for event in loaded.lifecycle_events if event.household_id == self._household_id
        ]
        self._pending: dict[str, AutomationEvent] = {
            event.event_id: event
            for event in loaded.pending_events
            if event.household_id == self._household_id
        }
        self._scheduler = ResourceActionScheduler(
            household_id=self._household_id,
            dispatch=dispatch,
        )
        self._scheduler.restore_state(loaded.scheduler_state)
        self._events: queue.Queue[AutomationEvent] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._feed.subscribe(self._on_event)
        self._save_locked()

    @property
    def household_id(self) -> str:
        return self._household_id

    @property
    def path(self) -> Path:
        return self._store.path

    def rules(self) -> tuple[AutomationRule, ...]:
        with self._lock:
            return tuple(self._rules.values())

    def lifecycle_events(self) -> tuple[AutomationLifecycleEvent, ...]:
        with self._lock:
            return tuple(self._lifecycle_events)

    def pending_events(self) -> tuple[AutomationEvent, ...]:
        with self._lock:
            return tuple(self._pending.values())

    def scheduler_status(self, *, now: datetime | None = None) -> list[ResourceScheduleStatus]:
        at = now or self._clock()
        with self._lock:
            rules = tuple(self._rules.values())
        return self._scheduler.status(rules, now=at, deadlines=self._deadline_snapshot())

    def propose(self, spec: AutomationSpec, *, rule_id: str) -> AutomationRule:
        if spec.household_id != self._household_id:
            raise ValueError("automation spec household does not match service household")
        rule = propose(spec, rule_id=rule_id)
        with self._lock:
            if rule.rule_id in self._rules:
                raise ValueError(f"automation rule already exists: {rule.rule_id}")
            self._rules[rule.rule_id] = rule
            self._save_locked()
        return rule

    def approve(
        self,
        rule_id: str,
        *,
        principal: Principal,
        justification: str,
        now: datetime | None = None,
    ) -> RuleTransitionResult:
        return self._transition(
            rule_id,
            lambda rule, sink, at: approve(
                rule, principal=principal, justification=justification, now=at, event_sink=sink
            ),
            now=now,
        )

    def revoke(
        self,
        rule_id: str,
        *,
        principal: Principal,
        justification: str,
        now: datetime | None = None,
    ) -> RuleTransitionResult:
        return self._transition(
            rule_id,
            lambda rule, sink, at: revoke(
                rule, principal=principal, justification=justification, now=at, event_sink=sink
            ),
            now=now,
        )

    def set_enabled(
        self,
        rule_id: str,
        enabled: bool,
        *,
        principal: Principal,
        justification: str,
        now: datetime | None = None,
    ) -> RuleTransitionResult:
        return self._transition(
            rule_id,
            lambda rule, sink, at: set_enabled(
                rule, enabled, principal=principal, justification=justification, now=at, event_sink=sink
            ),
            now=now,
        )

    def _transition(self, rule_id: str, transition, *, now: datetime | None) -> RuleTransitionResult:
        at = now or self._clock()
        audit_events: list[AutomationLifecycleEvent] = []
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                raise KeyError(rule_id)
            result = transition(rule, audit_events.append, at)
            self._lifecycle_events.extend(audit_events)
            if result.status is DecisionStatus.ALLOW:
                self._rules[rule_id] = result.rule
            self._save_locked()
            return result

    def tick(self, *, now: datetime | None = None) -> list[ResourceScheduleOutcome]:
        at = now or self._clock()
        with self._lock:
            rules = tuple(self._rules.values())
        outcomes = self._scheduler.tick(rules=rules, now=at, deadlines=self._deadline_snapshot())
        with self._lock:
            self._save_locked()
        return outcomes

    def process_pending(self, *, limit: int | None = None) -> list[ResourceScheduleOutcome]:
        """Consume queued persisted events synchronously, mainly for tests or recovery."""

        with self._lock:
            pending = list(self._pending.values())
        if limit is not None:
            pending = pending[:limit]
        outcomes: list[ResourceScheduleOutcome] = []
        for event in pending:
            outcomes.extend(self._process_event(event))
        return outcomes

    def _on_event(self, event: AutomationEvent) -> None:
        if event.household_id != self._household_id:
            return
        with self._lock:
            if event.event_id in self._pending:
                return
            self._pending[event.event_id] = event
            self._save_locked()
            running = self._worker is not None
        if running:
            self._events.put(event)

    def _process_event(self, event: AutomationEvent) -> list[ResourceScheduleOutcome]:
        with self._lock:
            rules = tuple(self._rules.values())
        try:
            outcomes = self._scheduler.handle_events(rules=rules, events=(event,), now=self._clock())
        except Exception:
            # Keep the event durably pending. A later recovery call can retry
            # it; the producer has already completed independently.
            return []
        with self._lock:
            self._pending.pop(event.event_id, None)
            self._save_locked()
        return outcomes

    def _deadline_snapshot(self) -> tuple[AutomationDeadline, ...]:
        provider = self._deadline_provider
        if provider is None:
            return ()
        try:
            values = provider()
        except Exception:
            # A failed/degraded deadline source must not disable unrelated
            # time/event automations or turn absence into a runnable signal.
            return ()
        return tuple(item for item in values if isinstance(item, AutomationDeadline))

    def start(self) -> None:
        with self._lock:
            if self._worker is not None:
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._worker_loop, name="haven-resource-automation", daemon=True)
            pending = tuple(self._pending.values())
            worker = self._worker
        for event in pending:
            self._events.put(event)
        worker.start()

    def _worker_loop(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            timeout = min(0.2, max(0.0, next_tick - time.monotonic()))
            try:
                event = self._events.get(timeout=timeout)
            except queue.Empty:
                if time.monotonic() >= next_tick:
                    try:
                        self.tick()
                    except Exception:
                        # A failed periodic evaluation must not kill the
                        # durable event consumer. Pending events remain
                        # persisted and can be recovered explicitly.
                        pass
                    next_tick = time.monotonic() + self._tick_interval
                continue
            try:
                self._process_event(event)
            finally:
                self._events.task_done()
            if time.monotonic() >= next_tick:
                try:
                    self.tick()
                except Exception:
                    pass
                next_tick = time.monotonic() + self._tick_interval

    def stop(self) -> None:
        with self._lock:
            worker = self._worker
            self._worker = None
            self._stop.set()
        if worker is not None:
            worker.join(timeout=2.0)

    def rebind_storage(self, path: str | Path) -> None:
        """Reload the moved installation sidecar after a data-dir change."""

        with self._lock:
            self._store = ResourceAutomationStore(path)
            loaded = self._store.load()
            self._rules = {
                rule.rule_id: rule for rule in loaded.rules if rule.spec.household_id == self._household_id
            }
            self._lifecycle_events = [
                event for event in loaded.lifecycle_events if event.household_id == self._household_id
            ]
            self._pending = {
                event.event_id: event for event in loaded.pending_events if event.household_id == self._household_id
            }
            self._scheduler = ResourceActionScheduler(
                household_id=self._household_id, dispatch=self._scheduler.dispatchers
            )
            self._scheduler.restore_state(loaded.scheduler_state)
            self._save_locked()

    def _save_locked(self) -> None:
        self._store.save(
            ResourceAutomationSnapshot(
                rules=tuple(self._rules.values()),
                lifecycle_events=tuple(self._lifecycle_events),
                pending_events=tuple(self._pending.values()),
                scheduler_state=self._scheduler.snapshot_state(),
            )
        )

    def close(self) -> None:
        self.stop()
        self._feed.unsubscribe(self._on_event)


__all__ = ["ResourceAutomationService"]
