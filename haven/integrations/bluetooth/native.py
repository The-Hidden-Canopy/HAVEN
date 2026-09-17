"""ctypes binding for the HAVEN-BT native ABI.

This binds the exact C functions and structures declared in
`native/haven-bt/include/haven_bt.h`. No native library ships with this
Python package: `CtypesBluetoothLibrary.load()` opens whatever
`havenbt`/`libhavenbt` is installed on the host, and there is currently
nothing to find, because no platform backend has been built (see
`native/haven-bt/README.md`). Every other piece of code in this module is
real and tested by mocking `ctypes.CDLL` -- the same pattern
`test_home_assistant_client.py` uses for `urlopen` -- which proves the
argument marshaling and calling convention are correct without requiring a
compiled library to exist.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import platform

HB_MAX_NAME_LEN = 64
HB_MAX_UUID_LEN = 37
HB_MAX_PAYLOAD_LEN = 512
HB_RSSI_UNKNOWN = -2147483648  # INT32_MIN


class HbStatus:
    OK = 0
    ERROR_INVALID_ARGUMENT = 1
    ERROR_NOT_FOUND = 2
    ERROR_NOT_CONNECTED = 3
    ERROR_TIMEOUT = 4
    ERROR_PERMISSION_DENIED = 5
    ERROR_ADAPTER_UNAVAILABLE = 6
    ERROR_UNSUPPORTED = 7
    ERROR_INTERNAL = 8


class HbEventType:
    DEVICE_FOUND = 0
    DEVICE_UPDATED = 1
    CONNECTED = 2
    DISCONNECTED = 3
    SERVICES_READY = 4
    NOTIFICATION = 5
    PAIRING_REQUEST = 6
    ERROR = 7


class HbEvent(ctypes.Structure):
    """Mirrors `hb_event` in haven_bt.h field-for-field."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("device", ctypes.c_uint64),
        ("rssi", ctypes.c_int32),
        ("name", ctypes.c_char * HB_MAX_NAME_LEN),
        ("characteristic_uuid", ctypes.c_char * HB_MAX_UUID_LEN),
        ("payload", ctypes.c_uint8 * HB_MAX_PAYLOAD_LEN),
        ("payload_len", ctypes.c_size_t),
        ("error", ctypes.c_int),
    ]


class NativeBluetoothError(RuntimeError):
    """Raised when a HAVEN-BT call returns a non-OK hb_status."""

    def __init__(self, function: str, status: int) -> None:
        super().__init__(f"{function} failed with hb_status {status}")
        self.function = function
        self.status = status


def _configure_prototypes(lib: ctypes.CDLL) -> None:
    """Set argtypes/restype for every hb_* entry point this binding calls.

    This is the one place the C signatures in haven_bt.h and this Python
    binding must agree; get one wrong here and ctypes either raises
    immediately (wrong arg count) or silently corrupts memory (wrong
    pointer type) -- so every entry point used below is configured, not
    left to ctypes' unchecked default of "assume everything is an int."
    """

    lib.hb_context_create.argtypes = []
    lib.hb_context_create.restype = ctypes.c_void_p

    lib.hb_context_destroy.argtypes = [ctypes.c_void_p]
    lib.hb_context_destroy.restype = None

    lib.hb_scan_start.argtypes = [ctypes.c_void_p]
    lib.hb_scan_start.restype = ctypes.c_int

    lib.hb_scan_stop.argtypes = [ctypes.c_void_p]
    lib.hb_scan_stop.restype = ctypes.c_int

    lib.hb_device_connect.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    lib.hb_device_connect.restype = ctypes.c_int

    lib.hb_device_disconnect.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    lib.hb_device_disconnect.restype = ctypes.c_int

    lib.hb_gatt_write.argtypes = [
        ctypes.c_void_p, ctypes.c_uint64, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    lib.hb_gatt_write.restype = ctypes.c_int

    lib.hb_gatt_read.argtypes = [
        ctypes.c_void_p, ctypes.c_uint64, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.hb_gatt_read.restype = ctypes.c_int

    lib.hb_poll_event.argtypes = [ctypes.c_void_p, ctypes.POINTER(HbEvent)]
    lib.hb_poll_event.restype = ctypes.c_int


class CtypesBluetoothLibrary:
    """The only class in Haven that calls `ctypes.CDLL` or touches a native
    HAVEN-BT library. Everything above this class (`BluetoothProvider`)
    depends on the plain-Python `NativeBluetoothLibrary` shape this class
    satisfies, not on ctypes.
    """

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._ctx = lib.hb_context_create()
        if not self._ctx:
            raise NativeBluetoothError("hb_context_create", HbStatus.ERROR_INTERNAL)

    @classmethod
    def load(cls, path: str | None = None) -> "CtypesBluetoothLibrary":
        resolved = path or ctypes.util.find_library("havenbt")
        if not resolved:
            raise FileNotFoundError(
                "could not locate a HAVEN-BT native library ('havenbt'); "
                "no platform backend has been built yet -- see native/haven-bt/README.md"
            )
        lib = ctypes.CDLL(resolved)
        _configure_prototypes(lib)
        return cls(lib)

    def close(self) -> None:
        """Destroy the native context and, on Windows, actually unload the DLL.

        `ctypes.CDLL` never calls `FreeLibrary` on its own -- garbage
        collecting the Python object leaves the DLL mapped into this
        process for its lifetime. That is invisible until something else
        needs the file unlocked (deleting it, replacing it with a rebuilt
        version), so it is fixed here rather than left as a surprise for a
        caller who assumes `close()` means closed.
        """

        self._lib.hb_context_destroy(self._ctx)
        if platform.system() == "Windows":
            handle = getattr(self._lib, "_handle", None)
            if isinstance(handle, int):
                # FreeLibrary's default argtypes treat the handle as a 32-bit
                # int, which overflows a real 64-bit module handle -- pass it
                # as c_void_p explicitly rather than relying on the default.
                free_library = ctypes.windll.kernel32.FreeLibrary
                free_library.argtypes = [ctypes.c_void_p]
                free_library(handle)

    def scan_start(self) -> int:
        return self._lib.hb_scan_start(self._ctx)

    def scan_stop(self) -> int:
        return self._lib.hb_scan_stop(self._ctx)

    def poll_event(self) -> HbEvent | None:
        event = HbEvent()
        got = self._lib.hb_poll_event(self._ctx, ctypes.byref(event))
        return event if got else None

    def device_connect(self, device_id: int) -> int:
        return self._lib.hb_device_connect(self._ctx, device_id)

    def device_disconnect(self, device_id: int) -> int:
        return self._lib.hb_device_disconnect(self._ctx, device_id)

    def gatt_write(self, device_id: int, characteristic_uuid: str, data: bytes) -> int:
        buf = (ctypes.c_uint8 * len(data))(*data)
        return self._lib.hb_gatt_write(
            self._ctx, device_id, characteristic_uuid.encode("ascii"), buf, len(data)
        )

    def gatt_read(self, device_id: int, characteristic_uuid: str, max_len: int = HB_MAX_PAYLOAD_LEN) -> bytes:
        buf = (ctypes.c_uint8 * max_len)()
        bytes_read = ctypes.c_size_t(0)
        status = self._lib.hb_gatt_read(
            self._ctx, device_id, characteristic_uuid.encode("ascii"), buf, max_len, ctypes.byref(bytes_read)
        )
        if status != HbStatus.OK:
            raise NativeBluetoothError("hb_gatt_read", status)
        return bytes(buf[: bytes_read.value])


__all__ = [
    "CtypesBluetoothLibrary",
    "HB_MAX_NAME_LEN",
    "HB_MAX_PAYLOAD_LEN",
    "HB_MAX_UUID_LEN",
    "HB_RSSI_UNKNOWN",
    "HbEvent",
    "HbEventType",
    "HbStatus",
    "NativeBluetoothError",
]
