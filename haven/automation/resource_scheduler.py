"""`ResourceActionScheduler`: the recurring-rule entry point resource
actions have never had (plan §14 P1: "home rules are mature,
`haven.actions.policy` has no recurring-rule equivalent for resource
actions yet").

Modeled directly on `haven.scheduler.engine.SchedulerEngine` -- "another
requester, never a privileged bypass." A due `AutomationRule` executes
nothing by itself: this engine only decides WHICH approved, enabled,
TIME-triggered automations are due right now, then asks a caller-supplied
per-domain dispatch callable to run the action through that domain's own
*existing* governed entry point (e.g. `ComputerActionService.request_action`
for `domain="computer"`) -- household scope, role, risk tier, and
confirmation-required all apply exactly as they do to a human-initiated
request, because it is the same code path. A run the dispatcher denies or
holds for confirmation records that honestly (`"denied"`/`"blocked"`); this
engine never possesses a confirmation token and can never manufacture one,
so a `CONFIRMATION_REQUIRED` resource action can only ever come back
blocked here -- an unattended schedule cannot consent to a destructive
action on a household member's behalf.

**`TriggerKind.TIME`, `TriggerKind.EVENT`, `TriggerKind.DEADLINE`,
`TriggerKind.EVIDENCE`, and `TriggerKind.EXTERNAL_CONDITION` are handled
here.** `tick()` handles TIME triggers (a
time-window check); `handle_events()` handles EVENT triggers (new file, new
message, provider state change, task status change -- plan §6.2's own list)
by matching a caller-supplied batch of `AutomationEvent`s against each
EVENT-triggered rule's `Trigger`/`Selector` parameters. Both paths dispatch
through the exact same per-domain callable and the exact same outcome
mapping, so an EVENT-triggered filesystem move is exactly as governed as a
TIME-triggered one. Production adapters in `haven/automation/emitters.py`
now publish those four event families through the server's bounded feed, and
the task service supplies deadline observations through the same composition
boundary.
`ResourceAutomationService` supplies explicit boot/restart wiring: it persists
pending deliveries and processed-event dedup state, consumes the feed in a
daemon worker, and ticks time rules on the same worker. Raw events and
stale/fallback/unavailable evidence are rejected before matching.
Evidence and external-condition triggers use the same trusted event delivery
path as EVENT rules, but only match the fixed event names emitted by the typed
adapters in `automation.emitters`. A raw event, a foreign household, or
degraded evidence remains ineligible.

**Household scope.** A scheduler is constructed for exactly one
`household_id`; foreign rules are ignored before due computation, status
projection, or dispatch. This is deliberately a required constructor input,
not an inferred value from the first rule or dispatcher, so a caller cannot
accidentally turn a mixed-household collection into an execution context.

**Dispatch contract.** Each `dispatch[domain]` callable takes the same
keyword shape `ComputerActionService.request_action` already exposes --
`action: str, resource_id: str | None, parameters: Mapping[str, Any],
justification: str`, returning that same `{"ok": ..., ...}` envelope -- so
wiring in a real domain service is a one-line lambda, not an adapter class.
`ActionTarget.parameters` may carry a reserved `"resource_id"` key (the one
thing `ResourceActionRequest` keeps as its own field rather than folding
into the parameter bag); it is extracted before the rest of the bag is
passed through unchanged.

The resource scheduler is composed by `HavenWebServer` rather than by the
home `HavenApplication`: the service owns the durable sidecar
`resource_automations.json`, feed worker, restart recovery, periodic tick,
and IPC lifecycle methods. The native desktop controls and additional domain
dispatch adapters remain separate follow-up work; the current production map
contains the governed computer-action adapter only.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from datetime import date, datetime, time, timedelta
import math
from typing import Any, Callable, Iterable, Mapping

from ..core.domain import RuleStatus
from ..core.time import require_aware_utc
from .deadlines import AutomationDeadline
from .events import AutomationEvent
from .lifecycle import AutomationRule
from .schema import ActionTarget, Trigger, TriggerKind

DEFAULT_COOLDOWN = timedelta(minutes=1)
NEXT_RUN_SCAN_DAYS = 8

DispatchFn = Callable[..., dict]


def _require_household_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("household_id must be a non-empty string")
    return value.strip()


def _schedule_trigger_from(trigger: Trigger) -> "_ScheduleWindow":
    """Reads a `TriggerKind.TIME` trigger's parameter bag into the same
    time-of-day/weekdays/window shape `haven.core.domain.ScheduleTrigger`
    already validates and checks -- a small local mirror rather than an
    import, since pulling in the device-vertical dataclass here would wire
    this module to `haven.core.domain.ScheduleTrigger`'s device-oriented
    docstring/module for a shape this module only needs three fields of.

    Expected keys: `time_of_day` (a `datetime.time`, or an "HH:MM" /
    "HH:MM:SS" string), `weekdays` (an iterable of ints, Monday=0..Sunday=6;
    absent or empty means every day), `window_minutes` (optional, default 5).
    Raises `ValueError` on anything malformed -- the caller is expected to
    treat one bad trigger as skippable, not fatal to a whole tick.
    """

    params = trigger.as_dict()
    raw_time = params.get("time_of_day")
    if isinstance(raw_time, time):
        time_of_day = raw_time
    elif isinstance(raw_time, str):
        parts = raw_time.strip().split(":")
        if len(parts) not in (2, 3):
            raise ValueError("time_of_day string must be 'HH:MM' or 'HH:MM:SS'")
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        time_of_day = time(hour=hour, minute=minute, second=second)
    else:
        raise ValueError("a TIME trigger requires a 'time_of_day' datetime.time or 'HH:MM[:SS]' string")

    raw_weekdays = params.get("weekdays") or ()
    weekdays = frozenset(int(day) for day in raw_weekdays)
    if any(not 0 <= day <= 6 for day in weekdays):
        raise ValueError("weekdays must be integers 0 (Monday) through 6 (Sunday)")

    window_minutes = params.get("window_minutes", 5)
    window = timedelta(minutes=float(window_minutes))
    if window <= timedelta(0):
        raise ValueError("window_minutes must be positive")

    return _ScheduleWindow(time_of_day=time_of_day, weekdays=weekdays, window=window)


@dataclass(frozen=True)
class _ScheduleWindow:
    time_of_day: time
    weekdays: frozenset[int]
    window: timedelta

    def is_due(self, at: datetime) -> bool:
        if self.weekdays and at.weekday() not in self.weekdays:
            return False
        today_start = datetime.combine(at.date(), self.time_of_day, tzinfo=at.tzinfo)
        return today_start <= at < today_start + self.window


def _matches_filters(filters: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    """Every `filters` key must be present in `payload` with an equal value.
    An empty `filters` matches anything -- the same "no selector means no
    narrowing" reading `Selector`'s own docstring implies. Fails closed
    (does not match) for a key `payload` never carries, rather than treating
    a missing key as a wildcard."""

    _MISSING = object()
    return all(payload.get(key, _MISSING) == value for key, value in filters.items())


def _event_trigger_matches(rule: AutomationRule, event: AutomationEvent) -> bool:
    """Whether `event` fires `rule`'s `TriggerKind.EVENT` trigger: same
    household, the trigger's own `event_name` matches exactly, and every
    other key in the trigger's parameters *and* the rule's `Selector`
    parameters must equal that key in the event's payload -- e.g.
    `Trigger(kind=EVENT, parameters={"event_name": "task.status_changed",
    "to_state": "done"})` only matches a task event whose payload's
    `to_state` is `"done"`, and a `Selector(parameters={"project_id": "p1"})`
    further narrows it to that one project's tasks."""

    if not event.is_eligible_for_automation:
        return False
    if rule.spec.trigger.kind != TriggerKind.EVENT:
        return False
    if rule.spec.household_id != event.household_id:
        return False
    params = rule.spec.trigger.as_dict()
    event_name = params.get("event_name")
    if not isinstance(event_name, str) or event_name != event.event_name:
        return False
    payload = event.as_dict()
    extra_filters = {key: value for key, value in params.items() if key != "event_name"}
    if not _matches_filters(extra_filters, payload):
        return False
    return _matches_filters(rule.spec.selector.as_dict(), payload)


def _evidence_trigger_matches(rule: AutomationRule, event: AutomationEvent) -> bool:
    if not event.is_eligible_for_automation or event.event_name != "evidence.changed":
        return False
    if rule.spec.trigger.kind != TriggerKind.EVIDENCE or rule.spec.household_id != event.household_id:
        return False
    params = rule.spec.trigger.as_dict()
    payload = event.as_dict()
    evidence_kind = params.get("evidence_kind")
    if evidence_kind is not None and payload.get("evidence_kind") != evidence_kind:
        return False
    minimum = params.get("min_confidence", 0.0)
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, (int, float))
        or not math.isfinite(float(minimum))
        or not 0.0 <= float(minimum) <= 1.0
    ):
        return False
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or confidence < float(minimum):
        return False
    filters = {key: value for key, value in params.items() if key not in {"evidence_kind", "min_confidence"}}
    return _matches_filters(filters, payload) and _matches_filters(rule.spec.selector.as_dict(), payload)


def _external_condition_trigger_matches(rule: AutomationRule, event: AutomationEvent) -> bool:
    if not event.is_eligible_for_automation or event.event_name != "external.condition.changed":
        return False
    if rule.spec.trigger.kind != TriggerKind.EXTERNAL_CONDITION or rule.spec.household_id != event.household_id:
        return False
    payload = event.as_dict()
    return _matches_filters(rule.spec.trigger.as_dict(), payload) and _matches_filters(rule.spec.selector.as_dict(), payload)


def _trigger_matches(rule: AutomationRule, event: AutomationEvent) -> bool:
    return (
        _event_trigger_matches(rule, event)
        or _evidence_trigger_matches(rule, event)
        or _external_condition_trigger_matches(rule, event)
    )


def _deadline_trigger_config(trigger: Trigger) -> tuple[str | None, str | None, float]:
    """Read the small, domain-independent deadline trigger shape.

    ``source_kind`` and ``source_id`` narrow the producer (for example,
    ``task`` and ``task:123``); either may be omitted. ``offset_minutes`` is
    relative to the observed due time: ``-15`` fires fifteen minutes before,
    ``0`` at the deadline, and ``15`` after it. Invalid configuration is
    skipped by the caller rather than disabling unrelated rules.
    """

    params = trigger.as_dict()
    source_kind = params.get("source_kind")
    if source_kind is not None:
        if not isinstance(source_kind, str) or not source_kind.strip():
            raise ValueError("deadline source_kind must be a non-empty string")
        source_kind = source_kind.strip()
    source_id = params.get("source_id")
    if source_id is not None:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("deadline source_id must be a non-empty string")
        source_id = source_id.strip()
    raw_offset = params.get("offset_minutes", 0.0)
    if isinstance(raw_offset, bool):
        raise ValueError("deadline offset_minutes must be a finite number")
    try:
        offset = float(raw_offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("deadline offset_minutes must be a finite number") from exc
    if not math.isfinite(offset):
        raise ValueError("deadline offset_minutes must be a finite number")
    return source_kind, source_id, offset


def _deadline_trigger_matches(rule: AutomationRule, deadline: AutomationDeadline) -> tuple[bool, float]:
    if not deadline.is_eligible_for_automation:
        return False, 0.0
    if rule.spec.trigger.kind != TriggerKind.DEADLINE:
        return False, 0.0
    if rule.spec.household_id != deadline.household_id:
        return False, 0.0
    try:
        source_kind, source_id, offset = _deadline_trigger_config(rule.spec.trigger)
    except ValueError:
        return False, 0.0
    if source_kind is not None and source_kind != deadline.source_kind:
        return False, offset
    if source_id is not None and source_id != deadline.source_id:
        return False, offset
    if not _matches_filters(rule.spec.selector.as_dict(), deadline.as_dict()):
        return False, offset
    return True, offset


def _dispatch_args(action: ActionTarget) -> tuple[str | None, dict[str, Any]]:
    params = dict(action.parameters)
    resource_id = params.pop("resource_id", None)
    return resource_id, params


def _outcome_for(result: Mapping[str, Any]) -> str:
    """Maps a dispatcher's `{"ok": ...}` envelope to the coarse status
    vocabulary the UI consumes -- the resource-action analog of
    `haven.scheduler.engine._outcome_for`. A `confirmation_required` result
    is deliberately reported as `"blocked"`, never treated as a green light:
    this engine holds no confirmation token and must never proceed as if it
    had one."""

    if not result.get("ok"):
        return "denied"
    if result.get("status") == "confirmation_required":
        return "blocked"
    if result.get("success") is True:
        return "executed"
    return "blocked"


@dataclass(frozen=True)
class ResourceScheduleOutcome:
    """One due automation's tick result, for the caller to log/surface."""

    rule_id: str
    outcome: str
    result: dict


@dataclass(frozen=True)
class ResourceScheduleStatus:
    """One scheduled resource automation's operational row, mirroring
    `haven.scheduler.engine.ScheduleStatus`'s shape for the equivalent
    device-rule surface."""

    rule_id: str
    domain: str
    action: str
    summary: str
    enabled: bool
    due_now: bool
    next_run_at: str | None
    last_fired_at: str | None
    last_outcome: str | None


class ResourceActionScheduler:
    """Decides which approved, enabled `AutomationRule`s are due (TIME or
    DEADLINE) or matched (EVENT), and dispatches each through its domain's own governed
    entry point. It is bound to one household; callers must create one
    scheduler per household rather than relying on pre-filtered input.

    Single-threaded by design, matching `SchedulerEngine`: `_last_fired`,
    `_last_outcome`, `_processed_events`, and `_processed_deadlines` are plain
    state needing no locking for one caller driving it from one place. The
    service persists the latter two collections across restart so a redelivered
    event or re-polled deadline cannot silently fire twice. `AutomationRule.enabled`
    (not a scheduler-side set) is the persistence seam for pause/resume, since
    unlike a device `Rule` it already carries that field.
    """

    def __init__(
        self,
        *,
        household_id: str,
        dispatch: Mapping[str, DispatchFn],
        cooldown: timedelta = DEFAULT_COOLDOWN,
        processed_event_limit: int = 4096,
    ) -> None:
        self._household_id = _require_household_id(household_id)
        if cooldown < timedelta(0):
            raise ValueError("cooldown must not be negative")
        if isinstance(processed_event_limit, bool) or not isinstance(processed_event_limit, int) or processed_event_limit < 1:
            raise ValueError("processed_event_limit must be a positive integer")
        self._dispatch = dict(dispatch)
        self._cooldown = cooldown
        self._processed_event_limit = processed_event_limit
        self._last_fired: dict[str, datetime] = {}
        self._last_outcome: dict[str, str] = {}
        # (rule_id, event_id) pairs already dispatched -- delivery dedup for
        # `handle_events`, unbounded in this pass the same way `_last_fired`
        # is: in-memory only, lost on restart, matching every other
        # operational-not-durable state this engine already accepts.
        self._processed_events: set[tuple[str, str]] = set()
        self._processed_event_order: deque[tuple[str, str]] = deque()
        # (rule_id, deadline_id, due_at, offset_minutes) is the durable
        # occurrence identity. A rescheduled source deadline therefore gets a
        # new occurrence while repeated polling of the same deadline does not.
        self._processed_deadlines: set[tuple[str, str, str, float]] = set()
        self._processed_deadline_order: deque[tuple[str, str, str, float]] = deque()

    @property
    def cooldown(self) -> timedelta:
        return self._cooldown

    @property
    def household_id(self) -> str:
        return self._household_id

    @property
    def dispatchers(self) -> dict[str, DispatchFn]:
        """A defensive copy of the governed domain dispatch map."""

        return dict(self._dispatch)

    def _rule_is_in_scope(self, rule: AutomationRule) -> bool:
        return rule.spec.household_id == self._household_id

    # -- due computation ------------------------------------------------------

    def due_rules(self, rules: Iterable[AutomationRule], *, now: datetime) -> list[AutomationRule]:
        now = require_aware_utc(now, name="scheduler check time")
        due: list[AutomationRule] = []
        for rule in rules:
            if not self._rule_is_in_scope(rule):
                continue
            if rule.status != RuleStatus.APPROVED or not rule.enabled:
                continue
            if rule.spec.trigger.kind != TriggerKind.TIME:
                continue
            try:
                window = _schedule_trigger_from(rule.spec.trigger)
            except ValueError:
                # A malformed trigger never blanks the rest of a tick -- the
                # same "one bad candidate never breaks the batch" posture
                # DiscoveryService.scan() and ModelRelationshipProposer
                # already apply elsewhere in this repo.
                continue
            if not window.is_due(now):
                continue
            if self._fired_recently(rule, window, now=now):
                continue
            due.append(rule)
        return due

    def due_deadlines(
        self,
        *,
        rules: Iterable[AutomationRule],
        deadlines: Iterable[AutomationDeadline],
        now: datetime,
    ) -> list[tuple[AutomationRule, AutomationDeadline, float]]:
        """Return due deadline occurrences not already delivered.

        A deadline is an occurrence, not a repeating poll result. The due
        time, source identity, and configured offset form its dedup key so a
        source that moves a deadline forward can legitimately create a new
        occurrence while an unchanged overdue item cannot refire on every
        worker tick.
        """

        now = require_aware_utc(now, name="deadline check time")
        matches: list[tuple[AutomationRule, AutomationDeadline, float]] = []
        rules = list(rules)
        for deadline in deadlines:
            if not isinstance(deadline, AutomationDeadline):
                continue
            for rule in rules:
                if not self._rule_is_in_scope(rule):
                    continue
                if rule.status != RuleStatus.APPROVED or not rule.enabled:
                    continue
                matched, offset = _deadline_trigger_matches(rule, deadline)
                if not matched or deadline.fire_at(offset) > now:
                    continue
                key = self._deadline_key(rule, deadline, offset)
                if key in self._processed_deadlines:
                    continue
                matches.append((rule, deadline, offset))
        return matches

    @staticmethod
    def _deadline_key(
        rule: AutomationRule, deadline: AutomationDeadline, offset: float
    ) -> tuple[str, str, str, float]:
        return (rule.rule_id, deadline.deadline_id, deadline.due_at.isoformat(), offset)

    def _remember_deadline(self, key: tuple[str, str, str, float]) -> None:
        if key in self._processed_deadlines:
            return
        self._processed_deadlines.add(key)
        self._processed_deadline_order.append(key)
        while len(self._processed_deadline_order) > self._processed_event_limit:
            self._processed_deadlines.discard(self._processed_deadline_order.popleft())

    def _fired_recently(self, rule: AutomationRule, window: _ScheduleWindow, *, now: datetime) -> bool:
        last = self._last_fired.get(rule.rule_id)
        if last is None:
            return False
        if now < last + self._cooldown:
            return True
        window_start = datetime.combine(now.date(), window.time_of_day, tzinfo=now.tzinfo)
        return window_start <= last < window_start + window.window

    # -- dispatching ------------------------------------------------------------

    def _dispatch_rule(self, rule: AutomationRule, *, justification: str, fired_at: datetime) -> ResourceScheduleOutcome:
        """Shared by `tick()` and `handle_events()`: dispatch one rule's
        action through its domain's real governed entry point and record the
        outcome. The rule is marked fired BEFORE dispatch runs, so a
        dispatcher that raises cannot re-fire on a later call -- the
        exception propagates to the caller; the rule stays marked fired."""

        self._last_fired[rule.rule_id] = fired_at
        action = rule.spec.action
        dispatcher = self._dispatch.get(action.domain)
        if dispatcher is None:
            result: dict = {"ok": False, "error": f"no dispatcher configured for domain {action.domain!r}"}
        else:
            resource_id, parameters = _dispatch_args(action)
            result = dispatcher(action=action.action, resource_id=resource_id, parameters=parameters, justification=justification)
        outcome = _outcome_for(result)
        self._last_outcome[rule.rule_id] = outcome
        return ResourceScheduleOutcome(rule_id=rule.rule_id, outcome=outcome, result=dict(result))

    def snapshot_state(self) -> dict[str, object]:
        """Return JSON-safe operational state for a restart boundary."""

        return {
            "last_fired": {rule_id: value.isoformat() for rule_id, value in self._last_fired.items()},
            "last_outcome": dict(self._last_outcome),
            "processed_events": [[rule_id, event_id] for rule_id, event_id in self._processed_event_order],
            "processed_deadlines": [
                [rule_id, deadline_id, due_at, offset]
                for rule_id, deadline_id, due_at, offset in self._processed_deadline_order
            ],
        }

    def restore_state(self, payload: Mapping[str, object] | None) -> None:
        """Restore scheduler memory without replaying or dispatching anything."""

        if not isinstance(payload, Mapping):
            return
        raw_fired = payload.get("last_fired", {})
        if isinstance(raw_fired, Mapping):
            for rule_id, value in raw_fired.items():
                if not isinstance(rule_id, str) or not isinstance(value, str):
                    continue
                try:
                    parsed = require_aware_utc(datetime.fromisoformat(value), name="restored scheduler time")
                except (TypeError, ValueError):
                    continue
                self._last_fired[rule_id] = parsed
        raw_outcome = payload.get("last_outcome", {})
        if isinstance(raw_outcome, Mapping):
            self._last_outcome.update(
                {rule_id: value for rule_id, value in raw_outcome.items() if isinstance(rule_id, str) and isinstance(value, str)}
            )
        raw_processed = payload.get("processed_events", ())
        if isinstance(raw_processed, (list, tuple)):
            for item in raw_processed:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                rule_id, event_id = item
                if not isinstance(rule_id, str) or not isinstance(event_id, str):
                    continue
                key = (rule_id, event_id)
                if key in self._processed_events:
                    continue
                self._processed_events.add(key)
                self._processed_event_order.append(key)
                while len(self._processed_event_order) > self._processed_event_limit:
                    self._processed_events.discard(self._processed_event_order.popleft())
        raw_deadlines = payload.get("processed_deadlines", ())
        if isinstance(raw_deadlines, (list, tuple)):
            for item in raw_deadlines:
                if not isinstance(item, (list, tuple)) or len(item) != 4:
                    continue
                rule_id, deadline_id, due_at, offset = item
                if not all(isinstance(value, str) for value in (rule_id, deadline_id, due_at)):
                    continue
                if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(float(offset)):
                    continue
                key = (rule_id, deadline_id, due_at, float(offset))
                if key in self._processed_deadlines:
                    continue
                self._processed_deadlines.add(key)
                self._processed_deadline_order.append(key)
                while len(self._processed_deadline_order) > self._processed_event_limit:
                    self._processed_deadlines.discard(self._processed_deadline_order.popleft())

    def tick(
        self,
        *,
        rules: Iterable[AutomationRule],
        now: datetime,
        deadlines: Iterable[AutomationDeadline] = (),
    ) -> list[ResourceScheduleOutcome]:
        """Dispatch due TIME and DEADLINE automations through governed paths."""

        now = require_aware_utc(now, name="scheduler tick time")
        rules = list(rules)
        outcomes = [
            self._dispatch_rule(rule, justification=f"schedule due at {now.isoformat()}", fired_at=now)
            for rule in self.due_rules(rules, now=now)
        ]
        outcomes.extend(self.handle_deadlines(rules=rules, deadlines=deadlines, now=now))
        return outcomes

    def handle_events(
        self, *, rules: Iterable[AutomationRule], events: Iterable[AutomationEvent], now: datetime
    ) -> list[ResourceScheduleOutcome]:
        """Match a batch of `AutomationEvent`s against every approved,
        enabled event-like rule, dispatching each match exactly
        once per `(rule, event)` pair -- redelivering the same event (an
        at-least-once emitter, a restart) never refires an automation twice.
        Rules and events are independent axes: one event can fire several
        rules, and this method makes no ordering guarantee between them
        beyond iterating events outer, rules inner."""

        now = require_aware_utc(now, name="event handling time")
        rules = list(rules)
        outcomes: list[ResourceScheduleOutcome] = []
        for event in events:
            for rule in rules:
                if not self._rule_is_in_scope(rule):
                    continue
                if rule.status != RuleStatus.APPROVED or not rule.enabled:
                    continue
                dedup_key = (rule.rule_id, event.event_id)
                if dedup_key in self._processed_events:
                    continue
                if not _trigger_matches(rule, event):
                    continue
                self._processed_events.add(dedup_key)
                self._processed_event_order.append(dedup_key)
                while len(self._processed_event_order) > self._processed_event_limit:
                    self._processed_events.discard(self._processed_event_order.popleft())
                outcomes.append(
                    self._dispatch_rule(
                        rule,
                        justification=f"triggered by {event.event_name!r} at {now.isoformat()}",
                        fired_at=now,
                    )
                )
        return outcomes

    def handle_deadlines(
        self,
        *,
        rules: Iterable[AutomationRule],
        deadlines: Iterable[AutomationDeadline],
        now: datetime,
    ) -> list[ResourceScheduleOutcome]:
        """Dispatch each due deadline occurrence at most once.

        The occurrence is recorded before dispatch, matching the existing
        scheduler rule that a dispatcher exception cannot cause an unbounded
        retry storm. A failed or confirmation-required action remains visible
        in the ordinary outcome/ledger path; this method never manufactures a
        confirmation or treats a blocked result as success.
        """

        now = require_aware_utc(now, name="deadline handling time")
        outcomes: list[ResourceScheduleOutcome] = []
        for rule, deadline, offset in self.due_deadlines(rules=rules, deadlines=deadlines, now=now):
            self._remember_deadline(self._deadline_key(rule, deadline, offset))
            outcomes.append(
                self._dispatch_rule(
                    rule,
                    justification=(
                        f"deadline {deadline.source_kind}:{deadline.source_id} due at "
                        f"{deadline.due_at.isoformat()} (offset {offset:g} minutes)"
                    ),
                    fired_at=now,
                )
            )
        return outcomes

    # -- projections --------------------------------------------------------

    def next_run(self, rule: AutomationRule, *, after: datetime) -> datetime | None:
        """The next due instant strictly after `after` (pure; scans 8 days).
        `None` for a non-TIME or malformed trigger -- there is nothing to
        project."""

        if not self._rule_is_in_scope(rule) or rule.spec.trigger.kind != TriggerKind.TIME:
            return None
        try:
            window = _schedule_trigger_from(rule.spec.trigger)
        except ValueError:
            return None
        after = require_aware_utc(after, name="next-run scan start")
        for offset in range(NEXT_RUN_SCAN_DAYS + 1):
            day: date = (after + timedelta(days=offset)).date()
            candidate = datetime.combine(day, window.time_of_day, tzinfo=after.tzinfo)
            if candidate <= after:
                continue
            if window.weekdays and candidate.weekday() not in window.weekdays:
                continue
            return candidate
        return None

    def status(
        self,
        rules: Iterable[AutomationRule],
        *,
        now: datetime,
        deadlines: Iterable[AutomationDeadline] = (),
    ) -> list[ResourceScheduleStatus]:
        """Operational rows for every supported trigger kind --
        not only TIME ones, so a future automations panel can show all of
        what this engine actually handles in one list. An EVENT rule's
        `due_now` is always `False` and `next_run_at` always `None`: it fires
        reactively, there is no time-window sense of "due" to report. A
        DEADLINE rule projects the earliest eligible source deadline, while
        evidence/external-condition rules are reactive and have no time
        projection, just like EVENT rules."""

        now = require_aware_utc(now, name="scheduler status time")
        rules = list(rules)
        due_ids = {rule.rule_id for rule in self.due_rules(rules, now=now)}
        deadlines = tuple(deadlines)
        due_deadline_matches = self.due_deadlines(rules=rules, deadlines=deadlines, now=now)
        due_ids.update(rule.rule_id for rule, _deadline, _offset in due_deadline_matches)
        rows: list[ResourceScheduleStatus] = []
        for rule in rules:
            if not self._rule_is_in_scope(rule):
                continue
            kind = rule.spec.trigger.kind
            if kind not in tuple(TriggerKind):
                continue
            if kind == TriggerKind.TIME:
                nxt = self.next_run(rule, after=now)
            elif kind == TriggerKind.DEADLINE:
                future: list[datetime] = []
                for deadline in deadlines:
                    matched, offset = _deadline_trigger_matches(rule, deadline)
                    if not matched:
                        continue
                    key = self._deadline_key(rule, deadline, offset)
                    fire_at = deadline.fire_at(offset)
                    if key not in self._processed_deadlines and fire_at > now:
                        future.append(fire_at)
                nxt = min(future) if future else None
            else:
                nxt = None
            last = self._last_fired.get(rule.rule_id)
            rows.append(
                ResourceScheduleStatus(
                    rule_id=rule.rule_id,
                    domain=rule.spec.action.domain,
                    action=rule.spec.action.action,
                    summary=" ".join(rule.spec.source_text.split()),
                    enabled=rule.enabled,
                    due_now=rule.rule_id in due_ids,
                    next_run_at=nxt.isoformat() if nxt is not None else None,
                    last_fired_at=last.isoformat() if last is not None else None,
                    last_outcome=self._last_outcome.get(rule.rule_id),
                )
            )
        return rows


__all__ = [
    # Re-exported from .events so a caller wiring an emitter into this
    # scheduler needs one import, not two.
    "AutomationEvent",
    "DEFAULT_COOLDOWN",
    "DispatchFn",
    "ResourceActionScheduler",
    "ResourceScheduleOutcome",
    "ResourceScheduleStatus",
]
