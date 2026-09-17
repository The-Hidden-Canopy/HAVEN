"""Execution routing: DeviceManifest.provider_id -> the adapter that runs it.

Before this, HavenRuntime always called one fixed `home_assistant` adapter
no matter what a device's manifest declared. Now a device with a registered
manifest routes through `execution_providers` by its `provider_id` --
proven here with two devices on two different fake providers executing
through the *same* rule/authority/receipt path in a single test. Every
existing test in the suite still constructs `HavenRuntime(home_assistant=...)`
alone, with no `execution_providers`, and is unaffected -- that is the
backward-compatibility contract this file's last two tests pin down.
"""

from datetime import timedelta

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import ActionKind, ContextState, DeviceState, PresenceState, RuleDraft, WorldSnapshot
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.execution import ExecutionProviderRegistry, UnknownExecutionProvider
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import FixtureModelGateway
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, RoleTier, _principal


class _FakeIrBlaster:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        from haven.core.domain import DeviceResult

        self.commands.append(command)
        return DeviceResult(success=True, detail="ir_code_sent", observed_at=command.requested_at, source="ir")


def _registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="bedroom_lights",
            device_type="light",
            provider_id="home_assistant",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="light.turn_on"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="old_window_ac",
            device_type="ac",
            provider_id="ir_blaster",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="ac.power_on"
                ),
            ),
        )
    )
    return registry


def _draft(principal, *, device_id: str, action_kind: ActionKind = ActionKind.ACTIVATE_SCENE) -> RuleDraft:
    return RuleDraft(
        draft_id=f"draft-{device_id}",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text=f"control {device_id}",
        interpretation=f"Operate {device_id} when the resident is present.",
        trigger_person_id=principal.actor_id,
        trigger_room_id="bedroom",
        required_context="working_late",
        action_kind=action_kind,
        target_device_id=device_id,
        capability="power",
    )


def _world(principal) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="routing-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=principal.actor_id, room_id="bedroom", present=True, observed_at=BASE_TIME, source="cam"
            ),
        ),
        contexts=(
            ContextState(context_id="working_late", active=True, observed_at=BASE_TIME, source="fixture.context"),
        ),
        devices=(
            DeviceState(
                device_id="bedroom_lights",
                kind="light",
                room_id="bedroom",
                is_on=False,
                brightness_pct=None,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
            ),
            DeviceState(
                device_id="old_window_ac",
                kind="ac",
                room_id="bedroom",
                is_on=False,
                brightness_pct=None,
                observed_at=BASE_TIME,
                source="fixture.ir_state",
            ),
        ),
    )


def _approve(runtime, draft, *, resident, owner):
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id, principal=owner, justification="Owner approval for routing.", now=BASE_TIME + timedelta(minutes=1)
    )
    return rule


def test_two_devices_on_two_providers_route_to_two_different_adapters():
    resident = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=resident.household_id)
    ha_adapter = FixtureHomeAssistant()
    ir_adapter = _FakeIrBlaster()
    providers = ExecutionProviderRegistry()
    providers.register("home_assistant", ha_adapter)
    providers.register("ir_blaster", ir_adapter)
    runtime = HavenRuntime(
        store=store,
        model_gateway=FixtureModelGateway(),
        authority=AuthorityEngine(device_registry=_registry()),
        execution_providers=providers,
    )
    world = _world(resident)

    light_rule = _approve(runtime, _draft(resident, device_id="bedroom_lights"), resident=resident, owner=owner)
    ac_rule = _approve(runtime, _draft(resident, device_id="old_window_ac"), resident=resident, owner=owner)

    light_receipt = runtime.run_rule(
        light_rule.rule_id, principal=resident, world=world, justification="Turn the light on.",
        now=BASE_TIME + timedelta(minutes=2),
    )
    ac_receipt = runtime.run_rule(
        ac_rule.rule_id, principal=resident, world=world, justification="Turn the AC on.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert light_receipt.outcome == "executed"
    assert ac_receipt.outcome == "executed"
    assert len(ha_adapter.commands) == 1
    assert ha_adapter.commands[0].target_device_id == "bedroom_lights"
    assert len(ir_adapter.commands) == 1
    assert ir_adapter.commands[0].target_device_id == "old_window_ac"


def test_unregistered_provider_fails_closed_into_a_failed_receipt_not_an_exception():
    resident = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=resident.household_id)
    providers = ExecutionProviderRegistry()
    providers.register("home_assistant", FixtureHomeAssistant())
    # ir_blaster is deliberately never registered.
    runtime = HavenRuntime(
        store=store,
        model_gateway=FixtureModelGateway(),
        authority=AuthorityEngine(device_registry=_registry()),
        execution_providers=providers,
    )
    rule = _approve(runtime, _draft(resident, device_id="old_window_ac"), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=_world(resident), justification="Attempt to run an unrouted device.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "allowed"
    assert receipt.outcome == "execution_failed"
    assert "UnknownExecutionProvider" in receipt.device_result.detail


def test_execution_providers_without_a_device_registry_falls_back_to_home_assistant():
    resident = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=resident.household_id)
    ha_adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        model_gateway=FixtureModelGateway(),
        home_assistant=ha_adapter,
        execution_providers=ExecutionProviderRegistry(),  # empty, and no device_registry on the default engine
    )
    from test_vertical_slice import _explicit_draft, _world as legacy_world

    rule = _approve(runtime, _explicit_draft(resident), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=legacy_world(resident),
        justification="Legacy ActionKind rule with no manifest at all.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert len(ha_adapter.commands) == 1


def test_runtime_requires_at_least_one_execution_path():
    store = HavenStore(household_id="household-a")
    with pytest.raises(ValueError):
        HavenRuntime(store=store, model_gateway=FixtureModelGateway())
