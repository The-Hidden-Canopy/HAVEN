"""Transport-agnostic Haven, end to end.

Haven should not think "BLE device" or "WiFi device" -- it should think
"fan," and reach it however `provider_id` says to. This test is that claim,
proved with everything already built: discover (BLE) -> enroll (a household
decision, not automatic authority) -> register for both risk classification
and execution -> observe presence from two independent transports (BLE
proximity, WiFi association) -> fuse them into one confidence -> drive a
real rule through the ordinary authority/execution path.
"""

from datetime import timedelta

from haven.authority.policy import AuthorityEngine
from haven.core.domain import ActionKind, ContextState, DeviceState, RuleDraft, WorldSnapshot
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceRegistry
from haven.discovery import DiscoveredDevice, FixtureDiscoveryProvider, enroll_device
from haven.execution import ExecutionProviderRegistry
from haven.intelligence.gateway import FixtureModelGateway
from haven.perception import FixtureObservationProvider, fuse_presence
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, RoleTier, _principal


class _FakeBleExecutionAdapter:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        from haven.core.domain import DeviceResult

        self.commands.append(command)
        return DeviceResult(success=True, detail="ble_gatt_write_ok", observed_at=command.requested_at, source="ble")


def test_a_fan_reached_over_ble_runs_through_the_same_pipeline_as_any_device():
    resident = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)

    # 1. Discovery produces a candidate, never authority.
    discovery = FixtureDiscoveryProvider(
        (
            DiscoveredDevice(
                candidate_id="desk_fan",
                provider_id="ble-bedroom",
                discovered_at=BASE_TIME,
                source="ble.scan",
                suggested_device_type="fan",
                suggested_room="bedroom",
                signal_strength=-52.0,
            ),
        )
    )
    (candidate,) = discovery.discover()

    # 2. Enrollment is the household's explicit decision.
    manifest = enroll_device(
        candidate,
        device_type="fan",
        capabilities=(
            CapabilityDescriptor(
                name="power", control_class=ControlClass.LOW_RISK, writable=True, service="ble.fan_power"
            ),
        ),
        approved_by=owner.actor_id,
        justification="Confirmed this is the bedroom desk fan before enrolling it.",
    )

    # 3. Registration: device_registry for risk classification, execution
    #    providers for routing -- both keyed by the same provider_id.
    device_registry = DeviceRegistry()
    device_registry.register(manifest)
    ble_adapter = _FakeBleExecutionAdapter()
    execution_providers = ExecutionProviderRegistry()
    execution_providers.register("ble-bedroom", ble_adapter)

    store = HavenStore(household_id=resident.household_id)
    runtime = HavenRuntime(
        store=store,
        model_gateway=FixtureModelGateway(),
        # This household explicitly opts into trusting fused BLE+WiFi presence
        # below full confidence; AuthorityEngine still fails closed by default.
        authority=AuthorityEngine(device_registry=device_registry, minimum_confidence=0.9),
        execution_providers=execution_providers,
    )

    # 4. Two independent transports observe the same fact about presence.
    observations = (
        FixtureObservationProvider(
            (
                _presence(resident.actor_id, "bedroom", confidence=0.72, source="ble.proximity"),
            )
        ).observe()
        + FixtureObservationProvider(
            (
                _presence(resident.actor_id, "bedroom", confidence=0.95, source="wifi.association"),
            )
        ).observe()
    )
    fused_presence = fuse_presence(observations, source="perception.fused_presence")
    assert fused_presence.confidence > 0.95  # noisy-OR: more confident than either transport alone

    world = WorldSnapshot(
        snapshot_id="transport-agnostic-snapshot",
        household_id=resident.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(fused_presence,),
        contexts=(ContextState(context_id="working_late", active=True, observed_at=BASE_TIME, source="fixture"),),
        devices=(
            DeviceState(
                device_id="desk_fan", kind="fan", room_id="bedroom", is_on=False, brightness_pct=None,
                observed_at=BASE_TIME, source="fixture.ble_state",
            ),
        ),
    )

    # 5. Ordinary rule lifecycle: the fan does not know or care it is BLE.
    draft = RuleDraft(
        draft_id="draft-desk-fan",
        household_id=resident.household_id,
        proposed_by=resident.actor_id,
        source_text="turn on the desk fan when I'm working late in the bedroom",
        interpretation="Turn on the desk fan when the resident is present and working_late is active.",
        trigger_person_id=resident.actor_id,
        trigger_room_id="bedroom",
        required_context="working_late",
        action_kind=ActionKind.ACTIVATE_SCENE,
        target_device_id="desk_fan",
        capability="power",
    )
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id, principal=owner, justification="Approve the desk fan rule.", now=BASE_TIME + timedelta(minutes=1)
    )

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=world, justification="Turn on the desk fan.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert len(ble_adapter.commands) == 1
    assert ble_adapter.commands[0].service == "ble.fan_power"


def _presence(person_id, room_id, *, confidence, source):
    from haven.core.domain import PresenceState

    return PresenceState(
        person_id=person_id, room_id=room_id, present=True, observed_at=BASE_TIME, source=source, confidence=confidence
    )
