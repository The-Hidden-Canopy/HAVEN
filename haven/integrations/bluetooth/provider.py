"""BluetoothProvider: DiscoveryProvider + ExecutionAdapter over HAVEN-BT.

This module has no `ctypes` import at all -- it depends only on
`NativeBluetoothLibrary`, a small Protocol that `native.CtypesBluetoothLibrary`
(real, ctypes-backed) and `FixtureBluetoothLibrary` (plain Python, for tests
and demonstrations) both satisfy. That is what makes `BluetoothProvider`
itself fully testable without any native library, compiled or otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from haven.core.domain import DeviceCommand, DeviceResult
from haven.discovery import DiscoveredDevice

BLUETOOTH_NATIVE_PROVIDER_ID = "bluetooth.native"
_RSSI_UNKNOWN = -2147483648  # matches native.HB_RSSI_UNKNOWN / HB_RSSI_UNKNOWN in haven_bt.h


class HbEventType:
    """Duplicated from `native.HbEventType` deliberately: this module must
    not import ctypes-adjacent code to stay testable without it."""

    DEVICE_FOUND = 0
    DEVICE_UPDATED = 1
    CONNECTED = 2
    DISCONNECTED = 3
    SERVICES_READY = 4
    NOTIFICATION = 5
    PAIRING_REQUEST = 6
    ERROR = 7


class HbStatus:
    OK = 0


class NativeBluetoothLibrary(Protocol):
    """What BluetoothProvider needs from a native library -- real or fake.

    Any object with these five methods works: `native.CtypesBluetoothLibrary`
    (backed by a real, currently nonexistent, compiled library) or a fixture
    with no native code behind it at all.
    """

    def scan_start(self) -> int: ...
    def scan_stop(self) -> int: ...
    def poll_event(self): ...
    def gatt_write(self, device_id: int, characteristic_uuid: str, data: bytes) -> int: ...


class FixtureBluetoothLibrary:
    """Deterministic local library used by tests and demonstrations.

    Events are plain objects (any object with `.type`/`.device`/`.rssi`
    attributes) rather than `native.HbEvent` ctypes structures -- proving
    `BluetoothProvider` never actually requires ctypes to function.
    """

    def __init__(self, events: tuple[object, ...] = (), *, write_status: int = HbStatus.OK) -> None:
        self._events = list(events)
        self._write_status = write_status
        self.writes: list[tuple[int, str, bytes]] = []
        self.scanning = False

    def scan_start(self) -> int:
        self.scanning = True
        return HbStatus.OK

    def scan_stop(self) -> int:
        self.scanning = False
        return HbStatus.OK

    def poll_event(self):
        return self._events.pop(0) if self._events else None

    def gatt_write(self, device_id: int, characteristic_uuid: str, data: bytes) -> int:
        self.writes.append((device_id, characteristic_uuid, data))
        return self._write_status


class BluetoothProvider:
    """A single BLE transport implementing HAVEN's DiscoveryProvider and
    ExecutionAdapter contracts.

    Device identity is intentionally simple: `DiscoveredDevice.candidate_id`
    is the native `hb_device_id` as a decimal string, and
    `haven.discovery.enroll_device()` already carries a candidate's id
    straight through to `DeviceManifest.device_id` -- so `execute()`
    recovers the native handle with `int(target_device_id)` and no separate
    id-mapping table is needed anywhere in this class.

    `CapabilityDescriptor.service` for a Bluetooth capability is the GATT
    characteristic UUID to write to; `DeviceCommand.parameters` must already
    contain the raw bytes under the key `"bytes"`. Encoding something like
    "brightness=50" into those bytes is a device-profile plugin's job, not
    this provider's -- both HAVEN-BT and this class stop at GATT.
    """

    def __init__(self, library: NativeBluetoothLibrary) -> None:
        self._library = library

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        found: dict[str, DiscoveredDevice] = {}
        self._library.scan_start()
        try:
            while True:
                event = self._library.poll_event()
                if event is None:
                    break
                if event.type not in (HbEventType.DEVICE_FOUND, HbEventType.DEVICE_UPDATED):
                    continue
                candidate_id = str(event.device)
                rssi = getattr(event, "rssi", _RSSI_UNKNOWN)
                found[candidate_id] = DiscoveredDevice(
                    candidate_id=candidate_id,
                    provider_id=BLUETOOTH_NATIVE_PROVIDER_ID,
                    discovered_at=datetime.now(timezone.utc),
                    source="bluetooth.native",
                    signal_strength=float(rssi) if rssi != _RSSI_UNKNOWN else None,
                )
        finally:
            self._library.scan_stop()
        return tuple(found.values())

    def execute(self, command: DeviceCommand) -> DeviceResult:
        try:
            device_id = int(command.target_device_id)
        except ValueError as exc:
            return self._result(command, success=False, detail=f"invalid_native_device_id:{exc}")

        payload = dict(command.parameters).get("bytes")
        if not isinstance(payload, (bytes, bytearray)):
            return self._result(command, success=False, detail="missing_bytes_parameter")

        status = self._library.gatt_write(device_id, command.service, bytes(payload))
        return self._result(command, success=status == HbStatus.OK, detail=f"hb_status:{status}")

    @staticmethod
    def _result(command: DeviceCommand, *, success: bool, detail: str) -> DeviceResult:
        return DeviceResult(
            success=success,
            detail=detail,
            observed_at=command.requested_at,
            source=BLUETOOTH_NATIVE_PROVIDER_ID,
        )


__all__ = [
    "BLUETOOTH_NATIVE_PROVIDER_ID",
    "BluetoothProvider",
    "FixtureBluetoothLibrary",
    "HbEventType",
    "HbStatus",
    "NativeBluetoothLibrary",
]
