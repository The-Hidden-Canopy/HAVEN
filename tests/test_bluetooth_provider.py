"""BluetoothProvider: DiscoveryProvider + ExecutionAdapter, no ctypes at all.

Exercised entirely against `FixtureBluetoothLibrary`, a plain-Python fake
with no native code behind it -- proving BluetoothProvider's own logic
(event translation, byte-payload relay, device-id recovery) is correct
independent of whether any real HAVEN-BT library exists.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from haven.core.domain import DeviceCommand
from haven.discovery import DiscoveredDevice, enroll_device
from haven.devices import CapabilityDescriptor, ControlClass
from haven.integrations.bluetooth import BLUETOOTH_NATIVE_PROVIDER_ID, BluetoothProvider, FixtureBluetoothLibrary
from haven.integrations.bluetooth.provider import HbEventType, HbStatus

BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


def _event(**overrides):
    defaults = dict(type=HbEventType.DEVICE_FOUND, device=42, rssi=-55)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _command(*, target_device_id: str = "42", payload: bytes | None = b"\x01\x32") -> DeviceCommand:
    parameters = (("bytes", payload),) if payload is not None else ()
    return DeviceCommand(
        request_id="request-1",
        target_device_id=target_device_id,
        service="0000fff1-0000-1000-8000-00805f9b34fb",
        parameters=parameters,
        requested_at=BASE_TIME,
    )


def test_discover_returns_candidates_from_found_and_updated_events():
    library = FixtureBluetoothLibrary((_event(device=42, rssi=-55), _event(type=HbEventType.DEVICE_UPDATED, device=42, rssi=-40)))
    provider = BluetoothProvider(library)

    found = provider.discover()

    assert len(found) == 1  # deduplicated by device id, same as SSDP
    candidate = found[0]
    assert isinstance(candidate, DiscoveredDevice)
    assert candidate.candidate_id == "42"
    assert candidate.provider_id == BLUETOOTH_NATIVE_PROVIDER_ID
    assert candidate.signal_strength == -40.0  # the later (UPDATED) reading wins
    assert library.scanning is False  # scan_stop was called


def test_discover_ignores_non_device_events():
    library = FixtureBluetoothLibrary((_event(type=HbEventType.CONNECTED, device=42),))
    provider = BluetoothProvider(library)

    assert provider.discover() == ()


def test_discover_treats_unknown_rssi_sentinel_as_no_signal():
    from haven.integrations.bluetooth.provider import _RSSI_UNKNOWN

    library = FixtureBluetoothLibrary((_event(device=7, rssi=_RSSI_UNKNOWN),))
    provider = BluetoothProvider(library)

    (candidate,) = provider.discover()
    assert candidate.signal_strength is None


def test_candidate_enrolls_and_execute_recovers_the_native_device_id():
    library = FixtureBluetoothLibrary((_event(device=99, rssi=-60),))
    provider = BluetoothProvider(library)
    (candidate,) = provider.discover()

    manifest = enroll_device(
        candidate,
        device_type="light",
        capabilities=(
            CapabilityDescriptor(
                name="power", control_class=ControlClass.LOW_RISK, writable=True,
                service="0000fff1-0000-1000-8000-00805f9b34fb",
            ),
        ),
        approved_by="owner-1",
        justification="Confirmed this is the bedroom BLE light before enrolling it.",
    )
    assert manifest.device_id == "99"  # candidate_id carried straight through

    result = provider.execute(_command(target_device_id=manifest.device_id))

    assert result.success is True
    assert library.writes == [(99, "0000fff1-0000-1000-8000-00805f9b34fb", b"\x01\x32")]


def test_execute_reports_a_failed_write_without_raising():
    library = FixtureBluetoothLibrary(write_status=HbStatus.OK + 1)
    provider = BluetoothProvider(library)

    result = provider.execute(_command())

    assert result.success is False
    assert "hb_status" in result.detail


def test_execute_requires_a_bytes_parameter():
    library = FixtureBluetoothLibrary()
    provider = BluetoothProvider(library)

    result = provider.execute(_command(payload=None))

    assert result.success is False
    assert result.detail == "missing_bytes_parameter"
    assert library.writes == []


def test_execute_rejects_a_non_numeric_target_device_id():
    library = FixtureBluetoothLibrary()
    provider = BluetoothProvider(library)

    result = provider.execute(_command(target_device_id="not-a-native-handle"))

    assert result.success is False
    assert "invalid_native_device_id" in result.detail
