"""DeviceManifest / CapabilityDescriptor dict round-trips and validation."""

import pytest

from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest


def _manifest() -> DeviceManifest:
    return DeviceManifest(
        device_id="washer",
        device_type="washer",
        provider_id="demo.house",
        capabilities=(
            CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="switch.turn_off"),
            CapabilityDescriptor(
                "cycle",
                ControlClass.MEDIUM,
                writable=True,
                values=("quick", "normal", "heavy"),
                service="washer.set_cycle",
            ),
            CapabilityDescriptor("door", ControlClass.READ, readable=True),
        ),
        telemetry=("energy_kwh",),
        semantic_role=None,
        room="laundry",
    )


def test_capability_descriptor_round_trip():
    capability = CapabilityDescriptor(
        "cycle", ControlClass.GUARDED, writable=True, values=("a", "b"), service="x.y"
    )
    assert CapabilityDescriptor.from_dict(capability.to_dict()) == capability


def test_manifest_round_trip_with_values_telemetry_and_nones():
    manifest = _manifest()
    payload = manifest.to_dict()
    assert payload["capabilities"][1]["control_class"] == "medium"
    assert payload["capabilities"][1]["values"] == ["quick", "normal", "heavy"]
    assert payload["telemetry"] == ["energy_kwh"]
    assert payload["semantic_role"] is None
    restored = DeviceManifest.from_dict(payload)
    assert restored == manifest
    assert restored.capability("cycle").values == ("quick", "normal", "heavy")


def test_from_dict_accepts_descriptor_instances():
    manifest = _manifest()
    payload = manifest.to_dict()
    payload["capabilities"] = list(manifest.capabilities)
    assert DeviceManifest.from_dict(payload) == manifest


def test_from_dict_rejects_garbage():
    manifest = _manifest()
    payload = manifest.to_dict()

    missing = dict(payload)
    del missing["device_id"]
    with pytest.raises(ValueError, match="device_id"):
        DeviceManifest.from_dict(missing)

    bad_class = dict(payload)
    bad_class["capabilities"] = [dict(c) for c in payload["capabilities"]]
    bad_class["capabilities"][0]["control_class"] = "superuser"
    with pytest.raises(ValueError, match="control_class"):
        DeviceManifest.from_dict(bad_class)

    dup = dict(payload)
    dup["capabilities"] = [dict(c) for c in payload["capabilities"]]
    dup["capabilities"][1] = dict(dup["capabilities"][0])
    with pytest.raises(ValueError, match="duplicate capability"):
        DeviceManifest.from_dict(dup)

    with pytest.raises(ValueError, match="mapping"):
        DeviceManifest.from_dict(["not", "a", "dict"])


def test_capability_from_dict_rejects_garbage():
    capability = CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="x.y")
    payload = capability.to_dict()

    missing = dict(payload)
    del missing["control_class"]
    with pytest.raises(ValueError, match="control_class"):
        CapabilityDescriptor.from_dict(missing)

    bad = dict(payload)
    bad["control_class"] = 42
    with pytest.raises(ValueError, match="control_class"):
        CapabilityDescriptor.from_dict(bad)

    bad_values = dict(payload)
    bad_values["values"] = "quick"
    with pytest.raises(ValueError, match="values"):
        CapabilityDescriptor.from_dict(bad_values)
