""""A recent explicit human action beats automation."

AuthorityEngine.decide() must deny an otherwise-allowed action against a
device a household member touched directly within the override window,
regardless of the action's risk tier -- and must stop denying it once the
window has passed.
"""

from datetime import timedelta

from haven.authority.policy import AuthorityEngine
from haven.core.domain import ChangeOrigin, ContextState, DeviceState, EvidenceStatus, PresenceState, WorldSnapshot
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import FixtureModelGateway
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, RoleTier, HavenStore, _explicit_draft, _principal


def _runtime_with_window(window: timedelta):
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        model_gateway=FixtureModelGateway(),
        home_assistant=adapter,
        authority=AuthorityEngine(human_override_window=window),
    )
    return runtime, store, adapter, principal, owner


def _world(principal, *, device_changed_by: ChangeOrigin, device_observed_at, now):
    return WorldSnapshot(
        snapshot_id="override-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(hours=6),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id="bedroom",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.presence",
            ),
        ),
        contexts=(
            ContextState(
                context_id="working_late",
                active=True,
                observed_at=BASE_TIME,
                source="fixture.context",
            ),
        ),
        devices=(
            DeviceState(
                device_id="bedroom_lights",
                kind="light",
                room_id="bedroom",
                is_on=True,
                brightness_pct=100,
                observed_at=device_observed_at,
                source="fixture.home_assistant_state",
                changed_by=device_changed_by,
            ),
        ),
    )


def _approved_rule(runtime, *, resident, owner):
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the bedroom light cap.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rule


def test_recent_human_change_blocks_an_otherwise_allowed_rule():
    runtime, store, adapter, resident, owner = _runtime_with_window(timedelta(minutes=90))
    rule = _approved_rule(runtime, resident=resident, owner=owner)
    now = BASE_TIME + timedelta(minutes=30)
    world = _world(resident, device_changed_by=ChangeOrigin.HUMAN, device_observed_at=BASE_TIME, now=now)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=world, justification="Apply the approved cap.", now=now
    )

    assert receipt.decision.code.value == "human_override_active"
    assert adapter.commands == ()


def test_override_expires_after_the_window_and_automation_resumes():
    runtime, store, adapter, resident, owner = _runtime_with_window(timedelta(minutes=90))
    rule = _approved_rule(runtime, resident=resident, owner=owner)
    now = BASE_TIME + timedelta(minutes=120)
    world = _world(resident, device_changed_by=ChangeOrigin.HUMAN, device_observed_at=BASE_TIME, now=now)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=world, justification="Apply the approved cap.", now=now
    )

    assert receipt.outcome == "executed"
    assert len(adapter.commands) == 1


def test_system_originated_change_never_triggers_an_override():
    runtime, store, adapter, resident, owner = _runtime_with_window(timedelta(minutes=90))
    rule = _approved_rule(runtime, resident=resident, owner=owner)
    now = BASE_TIME + timedelta(minutes=2)
    world = _world(resident, device_changed_by=ChangeOrigin.SYSTEM, device_observed_at=BASE_TIME, now=now)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=world, justification="Apply the approved cap.", now=now
    )

    assert receipt.outcome == "executed"


def test_override_window_is_configurable_per_engine():
    runtime, store, adapter, resident, owner = _runtime_with_window(timedelta(minutes=5))
    rule = _approved_rule(runtime, resident=resident, owner=owner)
    now = BASE_TIME + timedelta(minutes=10)
    world = _world(resident, device_changed_by=ChangeOrigin.HUMAN, device_observed_at=BASE_TIME, now=now)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=world, justification="Apply the approved cap.", now=now
    )

    assert receipt.outcome == "executed"
