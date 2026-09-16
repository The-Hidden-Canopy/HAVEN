import pytest

from haven.devices import (
    CapabilityDescriptor,
    ControlClass,
    DeviceManifest,
    DeviceRegistry,
    UnknownCapability,
    UnknownDevice,
)


def _washer_manifest() -> DeviceManifest:
    return DeviceManifest(
        device_id="washer_laundry",
        device_type="washer",
        provider_id="home_assistant",
        capabilities=(
            CapabilityDescriptor(name="status", control_class=ControlClass.READ, readable=True),
            CapabilityDescriptor(
                name="cycle",
                control_class=ControlClass.LOW_RISK,
                readable=True,
                writable=True,
                values=("normal", "delicate", "heavy"),
                service="washer.set_cycle",
            ),
            CapabilityDescriptor(
                name="start", control_class=ControlClass.MEDIUM, writable=True, service="washer.start"
            ),
            CapabilityDescriptor(
                name="pause", control_class=ControlClass.LOW_RISK, writable=True, service="washer.pause"
            ),
        ),
        telemetry=("remaining_minutes", "door_state", "cycle_state"),
    )


def test_manifest_exposes_capability_lookup():
    manifest = _washer_manifest()

    assert manifest.capability_names == ("status", "cycle", "start", "pause")
    assert manifest.has_capability("start", writable=True)
    assert not manifest.has_capability("start", readable=True)
    assert manifest.capability("cycle").values == ("normal", "delicate", "heavy")


def test_manifest_rejects_unknown_capability_lookup():
    manifest = _washer_manifest()

    with pytest.raises(UnknownCapability):
        manifest.capability("spin")


def test_capability_must_be_readable_or_writable():
    with pytest.raises(ValueError):
        CapabilityDescriptor(name="status", control_class=ControlClass.READ)


def test_writable_capability_must_declare_a_service():
    with pytest.raises(ValueError):
        CapabilityDescriptor(name="start", control_class=ControlClass.MEDIUM, writable=True)


def test_manifest_rejects_duplicate_capability_names():
    with pytest.raises(ValueError):
        DeviceManifest(
            device_id="dupe",
            device_type="tv",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="tv.turn_on"
                ),
                CapabilityDescriptor(name="power", control_class=ControlClass.LOW_RISK, readable=True),
            ),
        )


def test_registry_lookup_by_device_id():
    registry = DeviceRegistry()
    manifest = _washer_manifest()
    registry.register(manifest)

    assert registry.is_registered("washer_laundry")
    assert registry.get("washer_laundry") is manifest


def test_registry_unknown_device_raises():
    registry = DeviceRegistry()

    with pytest.raises(UnknownDevice):
        registry.get("nope")
    assert registry.is_registered("nope") is False


def test_registry_find_filters_by_type_and_capability():
    registry = DeviceRegistry()
    registry.register(_washer_manifest())
    registry.register(
        DeviceManifest(
            device_id="dryer_laundry",
            device_type="dryer",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="start", control_class=ControlClass.MEDIUM, writable=True, service="dryer.start"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="living_room_tv",
            device_type="tv",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="tv.turn_on"
                ),
            ),
        )
    )

    assert set(registry.find(requires_capability="start")) == {"washer_laundry", "dryer_laundry"}
    assert registry.find(device_type="tv") == ("living_room_tv",)
    assert registry.find(device_type="washer", requires_capability="power") == ()


def test_role_defaults_to_device_type_when_no_semantic_role_is_declared():
    manifest = _washer_manifest()

    assert manifest.semantic_role is None
    assert manifest.role == "washer"


def test_semantic_role_lets_a_switch_be_found_as_a_light():
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="bedroom_ceiling_light",
            device_type="light",
            provider_id="home_assistant",
            room="bedroom",
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
            provider_id="home_assistant",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="switch.turn_off"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="bedroom_fan_plug",
            device_type="switch",
            semantic_role="fan",
            provider_id="home_assistant",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="switch.turn_off"
                ),
            ),
        )
    )

    assert set(registry.find(role="light", room="bedroom")) == {
        "bedroom_ceiling_light",
        "bedroom_lamp_plug",
    }
    assert registry.find(device_type="light", room="bedroom") == ("bedroom_ceiling_light",)
    assert registry.find(role="fan") == ("bedroom_fan_plug",)
