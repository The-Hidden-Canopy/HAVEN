"""Scheduler engine: due computation, dedup, cooldown, context and expiry
gates, tick-through-runtime (executed and blocked), next-run projection, the
enabled set with JSON persistence, and status rows.

Doctrine under test: the scheduler is another requester, never a privileged
bypass -- a due schedule runs through ``HavenRuntime.run_rule`` and a policy
block records the ordinary BLOCKED receipt, while the engine only answers
WHICH approved rules are due to be asked right now.
"""

import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ActionStatus,
    ContextState,
    DecisionCode,
    DeviceSelector,
    DeviceState,
    EvidenceStatus,
    Principal,
    RoleTier,
    Rule,
    RuleDraft,
    ScheduleTrigger,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime
from haven.scheduler import ScheduleStatus, SchedulerEngine

UTC = timezone.utc
# 2026-09-16 is a Wednesday (weekday() == 2).
NOW = datetime(2026, 9, 16, 22, 35, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


def _principal(*, role: RoleTier = RoleTier.MEMBER) -> Principal:
    return Principal(actor_id="resident-1", household_id="household-a", role_tier=role)


def _runtime() -> tuple[HavenRuntime, HavenStore, FixtureHomeAssistant]:
    store = HavenStore(household_id="household-a")
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
        authority=AuthorityEngine(),
    )
    return runtime, store, adapter


def _light_off_draft(
    *,
    schedule: ScheduleTrigger | None = None,
    required_context: str | None = None,
    expires_at: datetime | None = None,
    target_selector: DeviceSelector | None = None,
) -> RuleDraft:
    kwargs: dict = {"target_device_id": "office_light"}
    if target_selector is not None:
        kwargs = {"target_selector": target_selector}
    return RuleDraft(
        draft_id="draft-light-off",
        household_id="household-a",
        proposed_by="resident-1",
        source_text="turn off the office light at 22:35",
        interpretation="Turn off the office light at 22:35.",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        schedule_trigger=schedule or ScheduleTrigger(time_of_day=time(22, 35), window=WINDOW),
        required_context=required_context,
        expires_at=expires_at,
        **kwargs,
    )


def _approve(runtime: HavenRuntime, draft: RuleDraft, *, owner: Principal, now: datetime) -> Rule:
    rule = runtime.propose_draft(draft, principal=_principal(), now=now)
    result = runtime.approve_rule(
        rule.rule_id, principal=owner, justification="Owner approved the schedule.", now=now
    )
    assert result.decision.status.value == "allow"
    return runtime.store.get_rule(rule.rule_id)


def _world(
    *,
    at: datetime,
    context_active: bool | None = None,
    device_status: EvidenceStatus = EvidenceStatus.OBSERVED,
) -> WorldSnapshot:
    contexts = ()
    if context_active is not None:
        contexts = (
            ContextState(
                context_id="quiet_hours",
                active=context_active,
                observed_at=at - timedelta(minutes=1),
                source="fixture",
            ),
        )
    return WorldSnapshot(
        snapshot_id="scheduler-snapshot",
        household_id="household-a",
        captured_at=at - timedelta(minutes=1),
        valid_until=at + timedelta(hours=1),
        contexts=contexts,
        devices=(
            DeviceState(
                device_id="office_light",
                kind="light",
                room_id="office",
                is_on=True,
                brightness_pct=70,
                observed_at=at - timedelta(minutes=1),
                source="fixture",
                status=device_status,
            ),
        ),
    )


def _engine(runtime: HavenRuntime, **kwargs) -> SchedulerEngine:
    return SchedulerEngine(runtime=runtime, principal=_principal(), **kwargs)


# -- construction -------------------------------------------------------------


def test_principal_must_be_a_member_or_higher():
    runtime, _, _ = _runtime()
    with pytest.raises(ValueError):
        SchedulerEngine(runtime=runtime, principal=_principal(role=RoleTier.GUEST))
    with pytest.raises(ValueError):
        SchedulerEngine(runtime=runtime, principal=_principal(), cooldown=timedelta(-1))


# -- due computation ------------------------------------------------------------


def test_due_rules_match_weekday_and_window():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    wednesdays = frozenset({2})
    rule = _approve(runtime, _light_off_draft(schedule=ScheduleTrigger(time_of_day=time(22, 35), weekdays=wednesdays, window=WINDOW)), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)

    world = _world(at=NOW)
    assert engine.due_rules([rule], world=world, now=NOW) == [rule]
    # Still inside the window.
    assert engine.due_rules([rule], world=world, now=NOW + WINDOW - timedelta(seconds=1)) == [rule]
    # Window closed.
    assert engine.due_rules([rule], world=world, now=NOW + WINDOW) == []
    # Same time, wrong weekday (Thursday).
    thursday = NOW + timedelta(days=1)
    assert engine.due_rules([rule], world=_world(at=thursday), now=thursday) == []
    # Not yet time today.
    earlier = NOW - timedelta(minutes=30)
    assert engine.due_rules([rule], world=_world(at=earlier), now=earlier) == []


def test_proposed_rules_and_rules_without_a_schedule_are_never_due():
    runtime, _, _ = _runtime()
    engine = _engine(runtime)
    proposed = runtime.propose_draft(_light_off_draft(), principal=_principal(), now=NOW - timedelta(days=1))
    presence_draft = RuleDraft(
        draft_id="draft-presence",
        household_id="household-a",
        proposed_by="resident-1",
        source_text="turn off the office light when resident leaves",
        interpretation="Turn off the office light when resident-1 leaves the office.",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        trigger_person_id="resident-1",
        trigger_room_id="office",
        target_device_id="office_light",
    )
    owner = _principal(role=RoleTier.OWNER)
    presence = _approve(runtime, presence_draft, owner=owner, now=NOW - timedelta(days=1))

    world = _world(at=NOW)
    assert engine.due_rules([proposed, presence], world=world, now=NOW) == []


def test_fired_window_dedup_via_last_fired():
    runtime, _, adapter = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(runtime, _light_off_draft(), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)

    receipts = engine.tick(world=_world(at=NOW), now=NOW)
    assert len(receipts) == 1
    # Still inside the same window instance: not due again.
    assert engine.due_rules([rule], world=_world(at=NOW + timedelta(minutes=2)), now=NOW + timedelta(minutes=2)) == []
    # The next day's window is a fresh due instant.
    tomorrow = NOW + timedelta(days=1)
    assert engine.due_rules([rule], world=_world(at=tomorrow), now=tomorrow) == [rule]


def test_cooldown_suppresses_a_refire_the_window_would_allow():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    always_due = ScheduleTrigger(time_of_day=time(22, 35), window=timedelta(hours=24))
    rule = _approve(runtime, _light_off_draft(schedule=always_due), owner=owner, now=NOW - timedelta(days=1))
    long_cooldown = _engine(runtime, cooldown=timedelta(hours=25))
    short_cooldown = _engine(runtime, cooldown=timedelta(minutes=1))

    next_day = NOW + timedelta(days=1)
    world = _world(at=next_day)
    long_cooldown.tick(world=_world(at=NOW), now=NOW)
    short_cooldown.tick(world=_world(at=NOW), now=NOW)

    # 24h later the trigger's own window would still be open, but the 25h
    # cooldown says the engine asked recently enough.
    assert long_cooldown.due_rules([rule], world=world, now=next_day) == []
    assert short_cooldown.due_rules([rule], world=world, now=next_day) == [rule]


def test_required_context_must_be_active():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(
        runtime,
        _light_off_draft(required_context="quiet_hours"),
        owner=owner,
        now=NOW - timedelta(days=1),
    )
    engine = _engine(runtime)

    assert engine.due_rules([rule], world=_world(at=NOW, context_active=True), now=NOW) == [rule]
    assert engine.due_rules([rule], world=_world(at=NOW, context_active=False), now=NOW) == []
    # No context evidence at all is not "active" either.
    assert engine.due_rules([rule], world=_world(at=NOW), now=NOW) == []


def test_expired_rule_is_skipped():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(
        runtime,
        _light_off_draft(expires_at=NOW - timedelta(seconds=1)),
        owner=owner,
        now=NOW - timedelta(days=1),
    )
    engine = _engine(runtime)

    assert engine.due_rules([rule], world=_world(at=NOW), now=NOW) == []
    rows = engine.status([rule], world=_world(at=NOW), now=NOW)
    assert rows[0].due_now is False


def test_disabled_rule_is_not_due_and_not_due_now():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(runtime, _light_off_draft(), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)

    engine.set_enabled(rule.rule_id, False)
    assert engine.is_enabled(rule.rule_id) is False
    assert engine.due_rules([rule], world=_world(at=NOW), now=NOW) == []
    assert engine.tick(world=_world(at=NOW), now=NOW) == []

    engine.set_enabled(rule.rule_id, True)
    assert engine.is_enabled(rule.rule_id) is True
    assert engine.due_rules([rule], world=_world(at=NOW), now=NOW) == [rule]


# -- tick through the real runtime ---------------------------------------------


def test_tick_executes_a_due_safe_automatic_schedule_through_the_governed_path():
    runtime, store, adapter = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(runtime, _light_off_draft(), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)

    receipts = engine.tick(world=_world(at=NOW), now=NOW)

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.outcome == "executed"
    # The governed path carried the schedule's justification and authority.
    assert receipt.requested_action.justification == f"schedule due at {NOW.isoformat()}"
    assert receipt.requested_action.rule_id == rule.rule_id
    assert receipt.requested_action.requested_by == engine.principal.actor_id
    assert adapter.commands[-1].service == "light.turn_off"
    executed = [action for action in store.state.actions if action.status == ActionStatus.EXECUTED]
    assert len(executed) == 1
    assert executed[0].request.target_device_id == "office_light"


def test_tick_records_the_block_and_does_not_refire_when_evidence_is_unavailable():
    runtime, store, adapter = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(runtime, _light_off_draft(), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)
    blind_world = _world(at=NOW, device_status=EvidenceStatus.UNAVAILABLE)

    receipts = engine.tick(world=blind_world, now=NOW)

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.decision.code == DecisionCode.EVIDENCE_UNAVAILABLE
    assert receipt.decision.status.value == "unavailable"
    assert receipt.outcome == "unavailable"
    # Fail closed: no command reached the execution adapter, but the block
    # was recorded through the ordinary path.
    assert adapter.commands == ()
    blocked = [action for action in store.state.actions if action.status == ActionStatus.BLOCKED]
    assert len(blocked) == 1
    # last_fired was still marked: a blocked run must not re-fire next tick.
    assert engine.tick(world=blind_world, now=NOW) == []
    rows = {row.rule_id: row for row in engine.status([rule], world=blind_world, now=NOW)}
    assert rows[rule.rule_id].last_fired_at == NOW.isoformat()
    assert rows[rule.rule_id].last_outcome == "unavailable"


def test_last_fired_survives_a_tick_that_completes_empty():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(runtime, _light_off_draft(), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)
    not_due = _world(at=NOW + timedelta(hours=1))

    assert engine.tick(world=not_due, now=NOW + timedelta(hours=1)) == []
    assert engine.status([rule], world=not_due, now=NOW + timedelta(hours=1))[0].last_fired_at is None


def test_tick_fans_a_selector_rule_out_to_its_resolved_devices():
    store = HavenStore(household_id="household-a")
    adapter = FixtureHomeAssistant()
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="office_light",
            device_type="light",
            provider_id="fixture",
            room="office",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
            ),
        )
    )
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
        authority=AuthorityEngine(device_registry=registry),
    )
    owner = _principal(role=RoleTier.OWNER)
    selector = DeviceSelector(role="light", room="office")
    rule = _approve(
        runtime,
        _light_off_draft(target_selector=selector),
        owner=owner,
        now=NOW - timedelta(days=1),
    )
    engine = _engine(runtime)

    receipts = engine.tick(world=_world(at=NOW), now=NOW)

    assert len(receipts) == 1
    assert receipts[0].outcome == "executed"
    assert adapter.commands[-1].target_device_id == "office_light"


# -- next_run -------------------------------------------------------------------


def test_next_run_scans_across_the_week_boundary():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    mondays = frozenset({0})
    rule = _approve(
        runtime,
        _light_off_draft(schedule=ScheduleTrigger(time_of_day=time(6, 30), weekdays=mondays)),
        owner=owner,
        now=NOW - timedelta(days=4),
    )
    engine = _engine(runtime)
    # 2026-09-18 is a Friday: the next Monday 6:30 is three days out.
    friday_noon = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

    assert engine.next_run(rule, after=friday_noon) == datetime(2026, 9, 21, 6, 30, tzinfo=UTC)
    # Later the same Monday: tomorrow's 6:30.
    monday_evening = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)
    assert engine.next_run(rule, after=monday_evening) == datetime(2026, 9, 28, 6, 30, tzinfo=UTC)


def test_next_run_is_none_without_a_trigger():
    runtime, _, _ = _runtime()
    engine = _engine(runtime)
    presence_draft = RuleDraft(
        draft_id="draft-presence",
        household_id="household-a",
        proposed_by="resident-1",
        source_text="x",
        interpretation="x",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        trigger_person_id="resident-1",
        trigger_room_id="office",
        target_device_id="office_light",
    )
    rule = runtime.propose_draft(presence_draft, principal=_principal(), now=NOW)
    assert engine.next_run(rule, after=NOW) is None


# -- enabled-set persistence ------------------------------------------------------


def test_enabled_set_persists_to_json_when_a_path_is_configured():
    runtime, _, _ = _runtime()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scheduler.json"
        first = _engine(runtime, state_path=path)
        first.set_enabled("rule-a", False)
        first.set_enabled("rule-b", False)
        first.set_enabled("rule-b", True)

        second = _engine(runtime, state_path=path)
        assert second.is_enabled("rule-a") is False
        assert second.is_enabled("rule-b") is True
        assert second.is_enabled("rule-c") is True


def test_enabled_set_is_in_memory_only_by_default():
    runtime, _, _ = _runtime()
    engine = _engine(runtime)
    engine.set_enabled("rule-a", False)

    fresh = _engine(runtime)
    assert fresh.is_enabled("rule-a") is True


# -- status rows --------------------------------------------------------------------


def test_status_reports_the_operational_fields():
    runtime, _, _ = _runtime()
    owner = _principal(role=RoleTier.OWNER)
    rule = _approve(runtime, _light_off_draft(), owner=owner, now=NOW - timedelta(days=1))
    engine = _engine(runtime)
    world = _world(at=NOW)
    just_after = NOW + timedelta(minutes=1)

    rows = engine.status([rule], world=world, now=just_after)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ScheduleStatus)
    assert row.rule_id == rule.rule_id
    assert row.summary == "turn off the office light at 22:35"
    assert row.enabled is True
    assert row.due_now is True
    assert row.next_run_at == (NOW + timedelta(days=1)).isoformat()
    assert row.last_fired_at is None
    assert row.last_outcome is None

    engine.tick(world=world, now=just_after)
    row = engine.status([rule], world=world, now=just_after)[0]
    assert row.due_now is False
    assert row.last_fired_at == just_after.isoformat()
    assert row.last_outcome == "executed"

    engine.set_enabled(rule.rule_id, False)
    row = engine.status([rule], world=world, now=just_after)[0]
    assert row.enabled is False
    assert row.due_now is False
