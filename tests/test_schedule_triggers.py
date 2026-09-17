"""Schedule-triggered rules: "every weekday at 6:30, warm the downstairs up
before I get up, but don't do it if nobody's home" is a schedule trigger
plus a context condition -- not a fourth trigger kind and not a new engine.
`ScheduleTrigger.is_due()` is a pure, stateless "is `at` inside the window"
check; Haven Core has no scheduler daemon and does not try to remember
whether a schedule already fired today, the same way it has no polling loop
for HomeAssistantObserver.
"""

from datetime import datetime, timedelta, time, timezone

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ContextState,
    DeviceState,
    RuleDraft,
    ScheduleTrigger,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime
from test_vertical_slice import RoleTier, _principal

UTC = timezone.utc
# 2026-09-16 is a Wednesday (weekday() == 2).
WEDNESDAY_630 = datetime(2026, 9, 16, 6, 30, tzinfo=UTC)
WEEKDAYS = frozenset({0, 1, 2, 3, 4})


def test_is_due_matches_within_the_window_on_a_matching_weekday():
    trigger = ScheduleTrigger(time_of_day=time(6, 30), weekdays=WEEKDAYS, window=timedelta(minutes=10))

    assert trigger.is_due(WEDNESDAY_630) is True
    assert trigger.is_due(WEDNESDAY_630 + timedelta(minutes=9)) is True
    assert trigger.is_due(WEDNESDAY_630 - timedelta(seconds=1)) is False
    assert trigger.is_due(WEDNESDAY_630 + timedelta(minutes=10)) is False


def test_is_due_respects_weekdays():
    saturday = WEDNESDAY_630 + timedelta(days=3)  # 2026-09-19 is a Saturday
    trigger = ScheduleTrigger(time_of_day=time(6, 30), weekdays=WEEKDAYS)

    assert trigger.is_due(saturday) is False


def test_empty_weekdays_means_every_day():
    saturday = WEDNESDAY_630 + timedelta(days=3)
    trigger = ScheduleTrigger(time_of_day=time(6, 30))

    assert trigger.is_due(saturday) is True


def test_schedule_trigger_rejects_a_non_positive_window():
    with pytest.raises(ValueError):
        ScheduleTrigger(time_of_day=time(6, 30), window=timedelta(0))


def test_schedule_trigger_rejects_out_of_range_weekdays():
    with pytest.raises(ValueError):
        ScheduleTrigger(time_of_day=time(6, 30), weekdays=frozenset({7}))


def test_rule_draft_trigger_kinds_are_mutually_exclusive():
    principal = _principal()
    base = dict(
        draft_id="draft-x",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="x",
        interpretation="x",
        action_kind=ActionKind.SET_THERMOSTAT,
        target_device_id="downstairs_hvac",
    )
    with pytest.raises(ValueError):
        RuleDraft(  # schedule + presence together
            **base,
            trigger_person_id=principal.actor_id,
            trigger_room_id="bedroom",
            schedule_trigger=ScheduleTrigger(time_of_day=time(6, 30)),
        )
    with pytest.raises(ValueError):
        RuleDraft(**base)  # no trigger at all


def _morning_draft(principal) -> RuleDraft:
    return RuleDraft(
        draft_id="draft-morning-warmup",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="every weekday morning, warm the downstairs up, unless nobody's home",
        interpretation="Set the downstairs thermostat to 70 on weekday mornings while someone is home.",
        action_kind=ActionKind.SET_THERMOSTAT,
        target_device_id="downstairs_hvac",
        parameters=(("target_temperature", 70),),
        schedule_trigger=ScheduleTrigger(time_of_day=time(6, 30), weekdays=WEEKDAYS, window=timedelta(minutes=10)),
        required_context="someone_home",
    )


def _runtime():
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store, intelligence_provider=ScriptedIntelligenceProvider(), home_assistant=adapter, authority=AuthorityEngine()
    )
    return runtime, store, adapter, principal, owner


def _world(*, household_id: str, someone_home: bool, at: datetime) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="morning-snapshot",
        household_id=household_id,
        captured_at=at - timedelta(minutes=1),
        valid_until=at + timedelta(hours=1),
        contexts=(
            ContextState(
                context_id="someone_home", active=someone_home, observed_at=at - timedelta(minutes=1), source="fixture"
            ),
        ),
        devices=(
            DeviceState(
                device_id="downstairs_hvac",
                kind="climate",
                room_id="downstairs",
                is_on=True,
                brightness_pct=None,
                observed_at=at - timedelta(minutes=1),
                source="fixture.home_assistant_state",
            ),
        ),
    )


def _approve(runtime, draft, *, resident, owner, now):
    rule = runtime.propose_draft(draft, principal=resident, now=now)
    runtime.approve_rule(
        rule.rule_id, principal=owner, justification="Owner approval for the morning warmup routine.", now=now
    )
    return rule


def test_scheduled_rule_fires_when_due_and_context_holds():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _morning_draft(resident), resident=resident, owner=owner, now=WEDNESDAY_630 - timedelta(days=1))

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(household_id=resident.household_id, someone_home=True, at=WEDNESDAY_630),
        justification="Weekday morning warmup, someone is home.",
        now=WEDNESDAY_630,
    )

    assert receipt.outcome == "executed"
    assert adapter.commands[0].parameters == (("target_temperature", 70),)


def test_scheduled_rule_does_not_fire_when_nobody_is_home():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _morning_draft(resident), resident=resident, owner=owner, now=WEDNESDAY_630 - timedelta(days=1))

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(household_id=resident.household_id, someone_home=False, at=WEDNESDAY_630),
        justification="It is 6:30am but nobody is home.",
        now=WEDNESDAY_630,
    )

    assert receipt.decision.code.value == "trigger_not_active"
    assert adapter.commands == ()


def test_scheduled_rule_does_not_fire_outside_the_window():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _morning_draft(resident), resident=resident, owner=owner, now=WEDNESDAY_630 - timedelta(days=1))
    late = WEDNESDAY_630 + timedelta(hours=1)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(household_id=resident.household_id, someone_home=True, at=late),
        justification="An hour after the window closed.",
        now=late,
    )

    assert receipt.decision.code.value == "trigger_not_active"
    assert adapter.commands == ()


def test_scheduled_rule_does_not_fire_on_the_weekend():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _morning_draft(resident), resident=resident, owner=owner, now=WEDNESDAY_630 - timedelta(days=1))
    saturday = WEDNESDAY_630 + timedelta(days=3)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(household_id=resident.household_id, someone_home=True, at=saturday),
        justification="Same time, but a Saturday.",
        now=saturday,
    )

    assert receipt.decision.code.value == "trigger_not_active"
    assert adapter.commands == ()
