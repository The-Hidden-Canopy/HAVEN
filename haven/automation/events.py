"""`AutomationEvent`: one concrete occurrence a `TriggerKind.EVENT`
automation can react to -- a new file, a new message, a provider state
change, a task status change (plan §6.2's own examples for this trigger
kind, and the exact four named in the "task/file/comms event triggers"
backlog item).

Deliberately one generic shape rather than four domain-specific event
classes: `event_name` plus a `payload` parameter bag (the same
`tuple[tuple[str, Any], ...]` shape `Trigger`/`Selector`/`ActionTarget`
already use) lets `ResourceActionScheduler.handle_events` match against it
generically, and lets a computer scan noticing a new resource, an email poll
noticing a new message, a provider health check noticing a state change, or a
task update crossing a status boundary produce one without this module
needing to know anything about where it came from --
the same open-vocabulary discipline `ResourceActionRequest.action` already
uses for provider actions.

`AutomationEventPublisher` is the trusted, household-scoped construction
boundary. `AutomationEventFeed` is the bounded in-process delivery seam that
production domain adapters use; a raw event value is intentionally ineligible
for scheduler execution.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
import threading
from typing import Any, Mapping

from ..core.domain import EvidenceStatus
from ..core.time import require_aware_utc


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _params_to_tuple(payload: Mapping[str, Any] | tuple[tuple[str, Any], ...]) -> tuple[tuple[str, Any], ...]:
    items = payload.items() if isinstance(payload, Mapping) else payload
    # A frozen dataclass only freezes the outer tuple. Snapshot nested values
    # too, otherwise a provider can mutate the mapping it passed after the
    # event was published and silently rewrite the evidence seen by a later
    # matcher or audit reader.
    return tuple((key, deepcopy(value)) for key, value in items)


_PUBLISHER_TOKEN = object()


@dataclass(frozen=True)
class AutomationEvent:
    """One occurrence, e.g. `event_name="file.created"` with a payload
    naming the resource. `event_id` is this occurrence's own identity, used
    for delivery dedup by `ResourceActionScheduler.handle_events` -- an
    at-least-once emitter redelivering the same event (e.g. after a
    restart) must not refire an automation a second time."""

    event_id: str
    event_name: str
    household_id: str
    occurred_at: datetime
    source: str = "untrusted"
    payload: tuple[tuple[str, Any], ...] = ()
    evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED
    _publisher_token: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, name="event_id"))
        object.__setattr__(self, "event_name", _require_text(self.event_name, name="event_name"))
        object.__setattr__(self, "household_id", _require_text(self.household_id, name="household_id"))
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at, name="occurred_at"))
        object.__setattr__(self, "source", _require_text(self.source, name="source"))
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ValueError("evidence_status must be an EvidenceStatus")
        object.__setattr__(self, "payload", _params_to_tuple(self.payload))

    @property
    def is_trusted(self) -> bool:
        """Whether this occurrence came through an explicit publisher boundary."""

        return self._publisher_token is _PUBLISHER_TOKEN

    @property
    def is_eligible_for_automation(self) -> bool:
        """Whether this event carries usable, non-degraded evidence."""

        return self.is_trusted and self.evidence_status not in {
            EvidenceStatus.STALE,
            EvidenceStatus.FALLBACK,
            EvidenceStatus.UNAVAILABLE,
        }

    def as_dict(self) -> dict[str, Any]:
        # Do not hand callers aliases into the event's retained evidence.
        return deepcopy(dict(self.payload))

    @classmethod
    def restore_published(
        cls,
        *,
        event_id: str,
        event_name: str,
        household_id: str,
        occurred_at: datetime,
        source: str,
        payload: Mapping[str, Any] | tuple[tuple[str, Any], ...] = (),
        evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED,
    ) -> "AutomationEvent":
        """Rehydrate a locally persisted publisher-stamped occurrence."""

        return cls(
            event_id=event_id,
            event_name=event_name,
            household_id=household_id,
            occurred_at=occurred_at,
            source=source,
            payload=payload,
            evidence_status=evidence_status,
            _publisher_token=_PUBLISHER_TOKEN,
        )


@dataclass(frozen=True)
class AutomationEventPublisher:
    """Scoped source boundary for creating scheduler-eligible events.

    A raw ``AutomationEvent`` is useful as a transport/value object but is
    deliberately not trusted by ``ResourceActionScheduler``. Domain adapters
    receive a publisher bound to one household and source, then publish only
    events they actually observed.
    """

    source: str
    household_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _require_text(self.source, name="source"))
        object.__setattr__(self, "household_id", _require_text(self.household_id, name="household_id"))

    def publish(
        self,
        *,
        event_id: str,
        event_name: str,
        occurred_at: datetime,
        payload: Mapping[str, Any] | tuple[tuple[str, Any], ...] = (),
        evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED,
    ) -> AutomationEvent:
        return AutomationEvent(
            event_id=event_id,
            event_name=event_name,
            household_id=self.household_id,
            occurred_at=occurred_at,
            source=self.source,
            payload=payload,
            evidence_status=evidence_status,
            _publisher_token=_PUBLISHER_TOKEN,
        )


class AutomationEventFeed:
    """Bounded delivery seam for trusted domain automation events.

    The feed deliberately stays in-process and bounded. It is a handoff
    between real observation adapters and a scheduler/consumer owned by the
    same HAVEN process, not a claim of durable event sourcing. Subscribers
    receive immutable, publisher-stamped events; a slow or failing consumer
    cannot break the mutation or observation that produced the event.
    """

    def __init__(
        self,
        *,
        source: str,
        household_id: str,
        clock,
        max_events: int = 256,
    ) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._publisher = AutomationEventPublisher(source=source, household_id=household_id)
        self._clock = clock
        self._events: deque[AutomationEvent] = deque(maxlen=max_events)
        self._listeners: list = []
        self._lock = threading.Lock()

    @property
    def household_id(self) -> str:
        return self._publisher.household_id

    def subscribe(self, listener) -> None:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def publish(
        self,
        *,
        event_id: str,
        event_name: str,
        occurred_at: datetime | None = None,
        payload: Mapping[str, Any] | tuple[tuple[str, Any], ...] = (),
        evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED,
    ) -> AutomationEvent:
        event = self._publisher.publish(
            event_id=event_id,
            event_name=event_name,
            occurred_at=occurred_at if occurred_at is not None else self._clock(),
            payload=payload,
            evidence_status=evidence_status,
        )
        with self._lock:
            self._events.append(event)
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Event delivery is advisory to the producing domain. A
                # scheduler failure must never roll back a task/file/mail
                # observation or mutation that already happened.
                continue
        return event

    def recent(self, *, limit: int = 100) -> tuple[AutomationEvent, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            return tuple(self._events)[-limit:]


__all__ = ["AutomationEvent", "AutomationEventFeed", "AutomationEventPublisher"]
