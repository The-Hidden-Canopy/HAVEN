"""Selector-based rules: "every light in the bedroom" instead of one device.

`run_rule_for_group()` resolves a `RuleDraft.target_selector` against the
device registry at run time and runs the ordinary single-device authority
and execution path once per resolved device, independently -- a GUARDED
device blocking on confirmation does not hold up a LOW_RISK device in the
same group.
"""

from datetime import timedelta

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    DeviceSelector,
    DeviceState,
    PresenceState,
    RoleTier,
    RuleDraft,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import FixtureModelGateway
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, _principal


def _bedroom_light_registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="bedroom_ceiling_light",
            device_type="light",
            room="bedroom",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="light.turn_off"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="bedroom_lamp_plug",
            device_type="switch",
            semantic_role="light",
            room="bedroom",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="switch.turn_off"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="bedroom_guarded_light",
            device_type="light",
            room="bedroom",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.GUARDED, writable=True, service="light.turn_off"
                ),
            ),
        )
    )
    # A living-room light must never show up in a bedroom-scoped selector.
    registry.register(
        DeviceManifest(
            device_id="living_room_light",
            device_type="light",
            room="living_room",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="light.turn_off"
                ),
            ),
        )
    )
    return registry


def _group_runtime(*, device_registry: DeviceRegistry):
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        model_gateway=FixtureModelGateway(),
        home_assistant=adapter,
        authority=AuthorityEngine(device_registry=device_registry),
    )
    return runtime, store, adapter, principal, owner


def _bedroom_lights_draft(principal) -> RuleDraft:
    return RuleDraft(
        draft_id="draft-bedroom-lights-off",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="turn off all the bedroom lights",
        interpretation="Turn off every device acting as a bedroom light when the resident leaves.",
        trigger_person_id=principal.actor_id,
        trigger_room_id="bedroom",
        required_context=None,
        action_kind=ActionKind.ACTIVATE_SCENE,
        target_selector=DeviceSelector(role="light", room="bedroom"),
        capability="power",
    )


def _bedroom_world(principal) -> WorldSnapshot:
    devices = tuple(
        DeviceState(
            device_id=device_id,
            kind="light",
            room_id="bedroom",
            is_on=True,
            brightness_pct=None,
            observed_at=BASE_TIME,
            source="fixture.home_assistant_state",
        )
        for device_id in ("bedroom_ceiling_light", "bedroom_lamp_plug", "bedroom_guarded_light")
    )
    return WorldSnapshot(
        snapshot_id="bedroom-group-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id="bedroom",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.presence",
            ),
        ),
        devices=devices,
    )


def _approve(runtime, draft, *, resident, owner):
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the bedroom-lights group rule.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rule


def test_selector_rule_requires_exactly_one_of_device_id_or_selector():
    principal = _principal()
    base_kwargs = dict(
        draft_id="draft-x",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="x",
        interpretation="x",
        trigger_person_id=principal.actor_id,
        trigger_room_id="bedroom",
        required_context=None,
        action_kind=ActionKind.ACTIVATE_SCENE,
    )
    with pytest.raises(ValueError):
        RuleDraft(**base_kwargs)  # neither target_device_id nor target_selector
    with pytest.raises(ValueError):
        RuleDraft(
            **base_kwargs,
            target_device_id="bedroom_lights",
            target_selector=DeviceSelector(role="light"),
        )


def test_group_rule_runs_independently_per_resolved_device():
    registry = _bedroom_light_registry()
    runtime, store, adapter, resident, owner = _group_runtime(device_registry=registry)
    rule = _approve(runtime, _bedroom_lights_draft(resident), resident=resident, owner=owner)

    receipts = runtime.run_rule_for_group(
        rule.rule_id,
        principal=resident,
        world=_bedroom_world(resident),
        justification="Leaving the bedroom.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    by_device = {receipt.requested_action.target_device_id: receipt for receipt in receipts}
    assert set(by_device) == {"bedroom_ceiling_light", "bedroom_lamp_plug", "bedroom_guarded_light"}
    assert "living_room_light" not in by_device

    assert by_device["bedroom_ceiling_light"].outcome == "executed"
    assert by_device["bedroom_lamp_plug"].outcome == "executed"
    assert by_device["bedroom_guarded_light"].decision.code.value == "confirmation_required"

    executed_services = {
        cmd.target_device_id: cmd.service for cmd in adapter.commands
    }
    assert executed_services == {
        "bedroom_ceiling_light": "light.turn_off",
        "bedroom_lamp_plug": "switch.turn_off",
    }


def test_run_rule_rejects_a_selector_based_rule():
    registry = _bedroom_light_registry()
    runtime, store, adapter, resident, owner = _group_runtime(device_registry=registry)
    rule = _approve(runtime, _bedroom_lights_draft(resident), resident=resident, owner=owner)

    with pytest.raises(ValueError):
        runtime.run_rule(
            rule.rule_id,
            principal=resident,
            world=_bedroom_world(resident),
            justification="Should use run_rule_for_group instead.",
            now=BASE_TIME + timedelta(minutes=2),
        )


def test_run_rule_for_group_rejects_a_single_device_rule():
    from test_vertical_slice import _explicit_draft, _runtime

    runtime, store, adapter, resident, owner = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Ordinary single-device approval.",
        now=BASE_TIME + timedelta(minutes=1),
    )

    with pytest.raises(ValueError):
        runtime.run_rule_for_group(
            rule.rule_id,
            principal=resident,
            world=_bedroom_world(resident),
            justification="Should use run_rule instead.",
            now=BASE_TIME + timedelta(minutes=2),
        )


def test_group_rule_resolves_to_nothing_without_a_device_registry():
    runtime, store, adapter, resident, owner = _group_runtime(device_registry=None)
    rule = _approve(runtime, _bedroom_lights_draft(resident), resident=resident, owner=owner)

    receipts = runtime.run_rule_for_group(
        rule.rule_id,
        principal=resident,
        world=_bedroom_world(resident),
        justification="No registry is wired in for this engine.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipts == ()
