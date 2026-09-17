"""AuthorityEngine risk resolution driven by a device's capability manifest.

These exercise the second, capability-shaped path through `decide()`
alongside the existing `ActionKind`-classified path in
test_vertical_slice.py / test_governance_boundaries.py, which are left
untouched: a request with `capability=None` still resolves risk from
`risk_for(action_kind)` exactly as before.
"""

from datetime import timedelta

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ActionStatus,
    DeviceState,
    EventType,
    PresenceState,
    RoleTier,
    RuleDraft,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, _principal


WASHER_ID = "washer_laundry"


def _washer_registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id=WASHER_ID,
            device_type="washer",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(name="status", control_class=ControlClass.READ, readable=True),
                CapabilityDescriptor(
                    name="start", control_class=ControlClass.MEDIUM, writable=True, service="washer.start"
                ),
                CapabilityDescriptor(
                    name="start_while_away",
                    control_class=ControlClass.GUARDED,
                    writable=True,
                    service="washer.start",
                ),
            ),
        )
    )
    return registry


def _capability_runtime(*, device_registry: DeviceRegistry):
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
        authority=AuthorityEngine(device_registry=device_registry),
    )
    return runtime, store, adapter, principal, owner


def _washer_draft(principal, *, capability: str) -> RuleDraft:
    return RuleDraft(
        draft_id=f"draft-{capability}",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text=f"run the washer capability {capability}",
        interpretation="Use the washer's declared capability when the resident is present.",
        trigger_person_id=principal.actor_id,
        trigger_room_id="laundry",
        required_context=None,
        action_kind=ActionKind.ACTIVATE_SCENE,
        target_device_id=WASHER_ID,
        capability=capability,
    )


def _washer_world(principal) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="washer-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id="laundry",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.presence",
            ),
        ),
        devices=(
            DeviceState(
                device_id=WASHER_ID,
                kind="washer",
                room_id="laundry",
                is_on=False,
                brightness_pct=None,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
            ),
        ),
    )


def _approve(runtime, store, draft, *, resident, owner):
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the washer capability rule.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rule


def test_medium_control_class_runs_without_a_confirmation_token() -> None:
    registry = _washer_registry()
    runtime, store, adapter, resident, owner = _capability_runtime(device_registry=registry)
    rule = _approve(runtime, store, _washer_draft(resident, capability="start"), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_washer_world(resident),
        justification="Start the washer now that it is approved.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "allowed"
    assert receipt.outcome == "executed"
    assert len(adapter.commands) == 1
    assert adapter.commands[0].service == "washer.start"
    assert store.state.actions[0].status == ActionStatus.EXECUTED


def test_guarded_control_class_requires_confirmation_like_forbidden_action_kind() -> None:
    registry = _washer_registry()
    runtime, store, adapter, resident, owner = _capability_runtime(device_registry=registry)
    rule = _approve(
        runtime, store, _washer_draft(resident, capability="start_while_away"), resident=resident, owner=owner
    )

    blocked = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_washer_world(resident),
        justification="Start the washer remotely.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert blocked.decision.code.value == "confirmation_required"
    assert adapter.commands == ()
    assert store.events[-1].event_type == EventType.ACTION_BLOCKED


def test_unknown_capability_fails_closed_rather_than_falling_back_to_action_kind() -> None:
    registry = _washer_registry()
    runtime, store, adapter, resident, owner = _capability_runtime(device_registry=registry)
    rule = _approve(
        runtime, store, _washer_draft(resident, capability="self_destruct"), resident=resident, owner=owner
    )

    blocked = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_washer_world(resident),
        justification="Attempt an undeclared capability.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert blocked.decision.code.value == "unknown_capability"
    assert adapter.commands == ()


def test_capability_action_without_a_registered_registry_fails_closed() -> None:
    runtime, store, adapter, resident, owner = _capability_runtime(device_registry=None)
    rule = _approve(runtime, store, _washer_draft(resident, capability="start"), resident=resident, owner=owner)

    blocked = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_washer_world(resident),
        justification="No device registry is wired in for this engine.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert blocked.decision.code.value == "unknown_capability"
    assert adapter.commands == ()
