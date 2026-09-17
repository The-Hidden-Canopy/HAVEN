"""Discovery produces a candidate, not authority.

`enroll_device()` is the only path from a `DiscoveredDevice` to a
`DeviceManifest`, and it is the enforcement point: there is no way to call
it without an approving actor and a justification.
"""

from datetime import datetime, timezone

import pytest

from haven.devices import CapabilityDescriptor, ControlClass
from haven.discovery import DiscoveredDevice, FixtureDiscoveryProvider, enroll_device

BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


def _candidate(**overrides) -> DiscoveredDevice:
    defaults = dict(
        candidate_id="ble:aa:bb:cc:dd:ee:ff",
        provider_id="ble-bedroom",
        discovered_at=BASE_TIME,
        source="ble.scan",
        suggested_device_type="fan",
        suggested_room="bedroom",
        signal_strength=-58.0,
    )
    defaults.update(overrides)
    return DiscoveredDevice(**defaults)


def test_discovery_provider_returns_whatever_it_was_given():
    provider = FixtureDiscoveryProvider((_candidate(),))

    found = provider.discover()

    assert len(found) == 1
    assert found[0].candidate_id == "ble:aa:bb:cc:dd:ee:ff"


def test_enroll_device_requires_a_non_empty_approver_and_justification():
    candidate = _candidate()
    capabilities = (
        CapabilityDescriptor(name="power", control_class=ControlClass.LOW_RISK, writable=True, service="ble.fan_power"),
    )

    with pytest.raises(ValueError):
        enroll_device(
            candidate, device_type="fan", capabilities=capabilities, approved_by="", justification="looks fine"
        )
    with pytest.raises(ValueError):
        enroll_device(
            candidate, device_type="fan", capabilities=capabilities, approved_by="owner-1", justification="   "
        )


def test_enroll_device_builds_a_manifest_from_household_supplied_capabilities():
    candidate = _candidate()
    capabilities = (
        CapabilityDescriptor(name="power", control_class=ControlClass.LOW_RISK, writable=True, service="ble.fan_power"),
        CapabilityDescriptor(
            name="speed",
            control_class=ControlClass.LOW_RISK,
            writable=True,
            values=("low", "medium", "high"),
            service="ble.fan_speed",
        ),
    )

    manifest = enroll_device(
        candidate,
        device_type="fan",
        capabilities=capabilities,
        approved_by="owner-1",
        justification="Confirmed this is the bedroom desk fan before enrolling it.",
    )

    assert manifest.device_id == "ble:aa:bb:cc:dd:ee:ff"
    assert manifest.device_type == "fan"
    assert manifest.provider_id == "ble-bedroom"
    assert manifest.room == "bedroom"  # inherited from the candidate's suggestion
    assert manifest.capability_names == ("power", "speed")


def test_enroll_device_lets_the_household_override_the_suggested_room():
    candidate = _candidate(suggested_room="bedroom")
    capabilities = (
        CapabilityDescriptor(name="power", control_class=ControlClass.LOW_RISK, writable=True, service="ble.fan_power"),
    )

    manifest = enroll_device(
        candidate,
        device_type="fan",
        capabilities=capabilities,
        approved_by="owner-1",
        justification="It was actually moved to the office.",
        room="office",
    )

    assert manifest.room == "office"


def test_discovered_device_requires_non_empty_identity():
    with pytest.raises(ValueError):
        DiscoveredDevice(candidate_id="", provider_id="ble-bedroom", discovered_at=BASE_TIME, source="ble.scan")
