"""Bluetooth: a real ctypes ABI binding, and the provider built on it.

`native.py` is the only module here that touches `ctypes.CDLL` -- there is
no compiled HAVEN-BT library to load yet (see `native/haven-bt/README.md`).
`provider.py`'s `BluetoothProvider` has no ctypes dependency at all; it is
fully exercised in tests against `FixtureBluetoothLibrary`.
"""

from .native import CtypesBluetoothLibrary, HbEvent, HbEventType, HbStatus, NativeBluetoothError
from .provider import BLUETOOTH_NATIVE_PROVIDER_ID, BluetoothProvider, FixtureBluetoothLibrary, NativeBluetoothLibrary

__all__ = [
    "BLUETOOTH_NATIVE_PROVIDER_ID",
    "BluetoothProvider",
    "CtypesBluetoothLibrary",
    "FixtureBluetoothLibrary",
    "HbEvent",
    "HbEventType",
    "HbStatus",
    "NativeBluetoothError",
    "NativeBluetoothLibrary",
]
