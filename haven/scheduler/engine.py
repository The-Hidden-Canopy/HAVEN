"""The HAVEN scheduler: another requester, never a privileged bypass.

A due schedule executes nothing by itself. It asks the runtime to run the
rule through the ordinary governed path (``HavenRuntime.run_rule``), so
household scope, role tier, the approved-rule gate, evidence
freshness/confidence, human override, risk tier, and confirmation all apply
unchanged. A run that policy blocks records the ordinary BLOCKED receipt and
event -- history shows the scheduler was told no, exactly the same as any
other requester. The scheduler answers only one question: WHICH approved
rules are due to be asked right now.

The scheduler's principal is deliberately unremarkable: a household MEMBER
or higher. What grants authority is the owner's approval of the RULE, not
the scheduler's role -- the engine holds no capability the resident could
not exercise directly.

Timing model: ``ScheduleTrigger.is_due`` is a pure, stateless window check,
so the engine layers the two pieces of memory the core deliberately lacks:
``last_fired`` (in-memory, per rule -- a rule already fired inside its
current window instance, or within ``cooldown`` of its last fire, is not due
again) and a disabled set (engine-side, optionally persisted as JSON). Both
are operational concerns of the caller, not of Haven Core, matching how the
core leaves every polling decision to a daemon.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from haven.audit.receipts import ActionReceipt
from haven.core.domain import (
    DecisionStatus,
    Principal,
    RoleTier,
    Rule,
    RuleStatus,
    WorldSnapshot,
)
from haven.core.time import require_aware_utc

if TYPE_CHECKING:
    from haven.runtime import HavenRuntime

DEFAULT_COOLDOWN = timedelta(minutes=1)
NEXT_RUN_SCAN_DAYS = 8


def _outcome_for(receipt: ActionReceipt) -> str:
    """Map a receipt to the coarse status vocabulary the UI consumes.

    ``run_rule`` never raises for a policy denial -- it returns a receipt --
    so every non-executed outcome here is an honest "the scheduler was told
    no" record, not an error.
    """

    if receipt.outcome == "executed":
        return "executed"
    if receipt.decision.status == DecisionStatus.UNAVAILABLE:
        return "unavailable"
    if receipt.decision.status == DecisionStatus.DENY:
        return "denied"
    return "blocked"


@dataclass(frozen=True)
class ScheduleStatus:
    """One scheduled rule's operational row for the web surface."""

    rule_id: str
    summary: str
    enabled: bool
    due_now: bool
    next_run_at: str | None
    last_fired_at: str | None
    last_outcome: str | None


class SchedulerEngine:
    """Decides which approved schedule-triggered rules are due, and asks.

    Single-threaded by design: the demo ticks from exactly one place at a
    time, and ``last_fired`` needs no locking for that. Construction starts
    every schedule fresh -- ``last_fired`` is in-memory state, so a restarted
    engine may ask again for a rule that fired just before the restart.
    """

    def __init__(
        self,
        *,
        runtime: HavenRuntime,
        principal: Principal,
        cooldown: timedelta = DEFAULT_COOLDOWN,
        state_path: str | Path | None = None,
    ) -> None:
        if not isinstance(principal, Principal) or principal.role_tier < RoleTier.MEMBER:
            raise ValueError("the scheduler principal must be a household member or higher")
        if cooldown < timedelta(0):
            raise ValueError("cooldown must not be negative")
        self._runtime = runtime
        self._principal = principal
        self._cooldown = cooldown
        self._state_path = Path(state_path) if state_path is not None else None
        self._last_fired: dict[str, datetime] = {}
        self._last_outcome: dict[str, str] = {}
        self._disabled: set[str] = self._load_disabled()

    @property
    def principal(self) -> Principal:
        return self._principal

    @property
    def cooldown(self) -> timedelta:
        return self._cooldown

    # -- the enabled set ----------------------------------------------------

    def is_enabled(self, rule_id: str) -> bool:
        return rule_id not in self._disabled

    def set_enabled(self, rule_id: str, enabled: bool) -> None:
        if enabled:
            self._disabled.discard(rule_id)
        else:
            self._disabled.add(rule_id)
        self._persist_disabled()

    def _load_disabled(self) -> set[str]:
        if self._state_path is None or not self._state_path.exists():
            return set()
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        disabled = data.get("disabled", [])
        if not isinstance(disabled, list):
            return set()
        return {str(item) for item in disabled}

    def _persist_disabled(self) -> None:
        if self._state_path is None:
            # In-memory only: the demo has no data dir, so its enabled set
            # lives and dies with the director, like the rest of demo state.
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"disabled": sorted(self._disabled)}, indent=2)
        self._state_path.write_text(payload + "\n", encoding="utf-8")

    # -- due computation ----------------------------------------------------

    def due_rules(self, rules: Iterable[Rule], *, world: WorldSnapshot, now: datetime) -> list[Rule]:
        """The approved schedule rules worth asking the runtime to run right now."""

        now = require_aware_utc(now, name="scheduler check time")
        due: list[Rule] = []
        for rule in rules:
            if rule.status != RuleStatus.APPROVED:
                continue
            trigger = rule.draft.schedule_trigger
            if trigger is None or not self.is_enabled(rule.rule_id):
                continue
            if not trigger.is_due(now):
                continue
            if self._fired_recently(rule, now=now):
                continue
            required = rule.draft.required_context
            if required is not None:
                context = world.context_for(required)
                if context is None or not context.active:
                    continue
            if rule.draft.expires_at is not None and now > rule.draft.expires_at:
                continue
            due.append(rule)
        return due

    def _fired_recently(self, rule: Rule, *, now: datetime) -> bool:
        last = self._last_fired.get(rule.rule_id)
        if last is None:
            return False
        trigger = rule.draft.schedule_trigger
        assert trigger is not None  # due_rules only asks about triggered rules
        # Either side of the "already asked" question suppresses a refire:
        # the engine-side cooldown floor, or a fire inside the trigger's
        # current window instance (the core's is_due stays true for the whole
        # window, so without this a periodic caller would re-ask daily-fresh).
        if now < last + self._cooldown:
            return True
        window_start = datetime.combine(now.date(), trigger.time_of_day, tzinfo=now.tzinfo)
        return window_start <= last < window_start + trigger.window

    # -- asking --------------------------------------------------------------

    def tick(self, *, world: WorldSnapshot, now: datetime) -> list[ActionReceipt]:
        """Run every due rule through the ordinary governed path.

        Each due rule is marked fired BEFORE it runs, so a run that raises
        cannot re-fire on every subsequent tick. ``run_rule`` does not raise
        for policy denials -- it returns the blocked receipt -- so genuine
        exceptions from the runtime propagate out of this loop to the caller;
        rules already marked fired stay marked, and rules not yet reached are
        still due next tick.
        """

        now = require_aware_utc(now, name="scheduler tick time")
        receipts: list[ActionReceipt] = []
        rules = list(self._runtime.store.state.rules)
        for rule in self.due_rules(rules, world=world, now=now):
            self._last_fired[rule.rule_id] = now
            justification = f"schedule due at {now.isoformat()}"
            if rule.draft.target_selector is not None:
                rule_receipts = list(
                    self._runtime.run_rule_for_group(
                        rule.rule_id,
                        principal=self._principal,
                        world=world,
                        justification=justification,
                        now=now,
                    )
                )
            else:
                rule_receipts = [
                    self._runtime.run_rule(
                        rule.rule_id,
                        principal=self._principal,
                        world=world,
                        justification=justification,
                        now=now,
                    )
                ]
            receipts.extend(rule_receipts)
            if rule_receipts:
                self._last_outcome[rule.rule_id] = self._group_outcome(rule_receipts)
        return receipts

    @staticmethod
    def _group_outcome(receipts: list[ActionReceipt]) -> str:
        for receipt in receipts:
            if receipt.outcome != "executed":
                return _outcome_for(receipt)
        return "executed"

    # -- projections ----------------------------------------------------------

    def next_run(self, rule: Rule, *, after: datetime) -> datetime | None:
        """The next due instant strictly after ``after`` (pure; scans 8 days)."""

        trigger = rule.draft.schedule_trigger
        if trigger is None:
            return None
        after = require_aware_utc(after, name="next-run scan start")
        for offset in range(NEXT_RUN_SCAN_DAYS + 1):
            day = (after + timedelta(days=offset)).date()
            candidate = datetime.combine(day, trigger.time_of_day, tzinfo=after.tzinfo)
            if candidate <= after:
                continue
            if trigger.weekdays and candidate.weekday() not in trigger.weekdays:
                continue
            return candidate
        return None

    def status(self, rules: Iterable[Rule], *, world: WorldSnapshot, now: datetime) -> list[ScheduleStatus]:
        """Operational rows for the scheduled rules, for the web surface."""

        now = require_aware_utc(now, name="scheduler status time")
        rules = list(rules)
        due_ids = {rule.rule_id for rule in self.due_rules(rules, world=world, now=now)}
        rows: list[ScheduleStatus] = []
        for rule in rules:
            if rule.draft.schedule_trigger is None:
                continue
            nxt = self.next_run(rule, after=now)
            last = self._last_fired.get(rule.rule_id)
            rows.append(
                ScheduleStatus(
                    rule_id=rule.rule_id,
                    summary=" ".join(rule.draft.source_text.split()),
                    enabled=self.is_enabled(rule.rule_id),
                    due_now=rule.rule_id in due_ids,
                    next_run_at=nxt.isoformat() if nxt is not None else None,
                    last_fired_at=last.isoformat() if last is not None else None,
                    last_outcome=self._last_outcome.get(rule.rule_id),
                )
            )
        return rows


__all__ = ["DEFAULT_COOLDOWN", "ScheduleStatus", "SchedulerEngine"]
