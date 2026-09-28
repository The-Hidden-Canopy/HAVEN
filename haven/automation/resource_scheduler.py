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

**`TriggerKind.TIME` and `TriggerKind.EVENT` this pass; evidence/deadline/
external-condition still not built.** `tick()` handles TIME triggers (a
time-window check); `handle_events()` handles EVENT triggers (new file, new
message, provider state change, task status change -- plan §6.2's own list)
by matching a caller-supplied batch of `AutomationEvent`s against each
EVENT-triggered rule's `Trigger`/`Selector` parameters. Both paths dispatch
through the exact same per-domain callable and the exact same outcome
mapping, so an EVENT-triggered filesystem move is exactly as governed as a
TIME-triggered one. **Nothing in this repo emits an `AutomationEvent`
yet** -- see `haven/automation/events.py`'s own docstring; this engine is
ready to consume a real feed once one exists (a computer scan noticing a
new resource, an email poll noticing a new message, a provider health check,
a task update), not a running subscription to one. Evidence/deadline/
external-condition triggers remain unhandled by either method; a rule using
one of those kinds is simply never due and never event-matched here.

**Dispatch contract.** Each `dispatch[domain]` callable takes the same
keyword shape `ComputerActionService.request_action` already exposes --
`action: str, resource_id: str | None, parameters: Mapping[str, Any],
justification: str`, returning that same `{"ok": ..., ...}` envelope -- so
wiring in a real domain service is a one-line lambda, not an adapter class.
`ActionTarget.parameters` may carry a reserved `"resource_id"` key (the one
thing `ResourceActionRequest` keeps as its own field rather than folding
into the parameter bag); it is extracted before the rest of the bag is
passed through unchanged.

**Not wired into `HavenApplication`'s boot sequence, native UI, or HTTP/IPC
surface in this pass.** `SchedulerEngine`'s own wiring (`start_scheduler`'s
background thread, `run_scheduler_tick`, the web/native automations panel)
touches a very large, safety-critical file end to end; doing that in the
same breath as introducing this engine -- with no interactive way in this
environment to click through the result -- is exactly the kind of
large-blast-radius change this repo's own convention (see `schema.py`,
the model proposer, the unified receipt item) deliberately defers to a
follow-up pass. What exists here is a complete, independently tested engine
ready for that wiring: construct one, hand it real `AutomationRule`s and a
real per-domain dispatch map, and `tick()` is a genuine end-to-end pass
through the real authority pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable, Mapping

from ..core.domain import RuleStatus
from ..core.time import require_aware_utc
from .events import AutomationEvent
from .lifecycle import AutomationRule
from .schema import ActionTarget, Trigger, TriggerKind

DEFAULT_COOLDOWN = timedelta(minutes=1)
NEXT_RUN_SCAN_DAYS = 8

DispatchFn = Callable[..., dict]


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
    """Decides which approved, enabled `AutomationRule`s are due (TIME) or
    matched (EVENT), and dispatches each through its domain's own governed
    entry point.

    Single-threaded by design, matching `SchedulerEngine`: `_last_fired`/
    `_last_outcome`/`_processed_events` are plain in-memory state needing no
    locking for one caller driving it from one place. All of it is
    operational, in-memory-only state -- a restarted process may ask again
    for a TIME automation that fired just before restart, or re-evaluate an
    EVENT it already dedup'd, the same honest boundary `SchedulerEngine`
    accepts. `AutomationRule.enabled` (not a scheduler-side set) is the
    persistence seam for pause/resume, since unlike a device `Rule` it
    already carries that field -- whatever store holds the rule collection
    this engine is handed is responsible for persisting it.
    """

    def __init__(self, *, dispatch: Mapping[str, DispatchFn], cooldown: timedelta = DEFAULT_COOLDOWN) -> None:
        if cooldown < timedelta(0):
            raise ValueError("cooldown must not be negative")
        self._dispatch = dict(dispatch)
        self._cooldown = cooldown
        self._last_fired: dict[str, datetime] = {}
        self._last_outcome: dict[str, str] = {}
        # (rule_id, event_id) pairs already dispatched -- delivery dedup for
        # `handle_events`, unbounded in this pass the same way `_last_fired`
        # is: in-memory only, lost on restart, matching every other
        # operational-not-durable state this engine already accepts.
        self._processed_events: set[tuple[str, str]] = set()

    @property
    def cooldown(self) -> timedelta:
        return self._cooldown

    # -- due computation ------------------------------------------------------

    def due_rules(self, rules: Iterable[AutomationRule], *, now: datetime) -> list[AutomationRule]:
        now = require_aware_utc(now, name="scheduler check time")
        due: list[AutomationRule] = []
        for rule in rules:
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

    def tick(self, *, rules: Iterable[AutomationRule], now: datetime) -> list[ResourceScheduleOutcome]:
        """Dispatch every due (`TriggerKind.TIME`) automation through its
        domain's real governed entry point."""

        now = require_aware_utc(now, name="scheduler tick time")
        return [
            self._dispatch_rule(rule, justification=f"schedule due at {now.isoformat()}", fired_at=now)
            for rule in self.due_rules(rules, now=now)
        ]

    def handle_events(
        self, *, rules: Iterable[AutomationRule], events: Iterable[AutomationEvent], now: datetime
    ) -> list[ResourceScheduleOutcome]:
        """Match a batch of `AutomationEvent`s against every approved,
        enabled, `TriggerKind.EVENT` rule, dispatching each match exactly
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
                if rule.status != RuleStatus.APPROVED or not rule.enabled:
                    continue
                dedup_key = (rule.rule_id, event.event_id)
                if dedup_key in self._processed_events:
                    continue
                if not _event_trigger_matches(rule, event):
                    continue
                self._processed_events.add(dedup_key)
                outcomes.append(
                    self._dispatch_rule(
                        rule,
                        justification=f"triggered by {event.event_name!r} at {now.isoformat()}",
                        fired_at=now,
                    )
                )
        return outcomes

    # -- projections --------------------------------------------------------

    def next_run(self, rule: AutomationRule, *, after: datetime) -> datetime | None:
        """The next due instant strictly after `after` (pure; scans 8 days).
        `None` for a non-TIME or malformed trigger -- there is nothing to
        project."""

        if rule.spec.trigger.kind != TriggerKind.TIME:
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

    def status(self, rules: Iterable[AutomationRule], *, now: datetime) -> list[ResourceScheduleStatus]:
        """Operational rows for every TIME- or EVENT-triggered automation --
        not only TIME ones, so a future automations panel can show all of
        what this engine actually handles in one list. An EVENT rule's
        `due_now` is always `False` and `next_run_at` always `None`: it
        fires reactively, there is no time-window sense of "due" to report.
        Evidence/deadline/external-condition rules are omitted entirely --
        reporting a due/next-run projection for a trigger kind this engine
        cannot evaluate would be exactly the false capability this repo's
        own discipline forbids."""

        now = require_aware_utc(now, name="scheduler status time")
        rules = list(rules)
        due_ids = {rule.rule_id for rule in self.due_rules(rules, now=now)}
        rows: list[ResourceScheduleStatus] = []
        for rule in rules:
            kind = rule.spec.trigger.kind
            if kind not in (TriggerKind.TIME, TriggerKind.EVENT):
                continue
            nxt = self.next_run(rule, after=now) if kind == TriggerKind.TIME else None
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
