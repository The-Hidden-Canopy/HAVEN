"""CtypesBluetoothLibrary: the ctypes binding against haven_bt.h.

There is no compiled HAVEN-BT library anywhere to load, so every test here
mocks `ctypes.CDLL`/`ctypes.util.find_library` the same way
`test_home_assistant_client.py` mocks `urlopen` -- proving the argument
marshaling and calling convention this binding uses are correct, not that
any real native library behaves correctly (none exists).
"""

import ctypes
from unittest.mock import MagicMock, patch

import pytest

from haven.integrations.bluetooth.native import (
    CtypesBluetoothLibrary,
    HbStatus,
    NativeBluetoothError,
    _find_dev_build,
)


def _fake_lib() -> MagicMock:
    lib = MagicMock()
    lib.hb_context_create.return_value = 0x1234  # a nonzero "pointer"
    return lib


def test_load_raises_when_no_library_can_be_found():
    with patch("haven.integrations.bluetooth.native.ctypes.util.find_library", return_value=None):
        with pytest.raises(FileNotFoundError):
            CtypesBluetoothLibrary.load()


def test_load_falls_back_to_a_local_dev_build_when_nothing_is_on_path(tmp_path):
    """A developer who just ran `build_windows.cmd` locally should not also
    have to fix up PATH/find_library to exercise the real backend."""

    build_dir = tmp_path / "native" / "haven-bt" / "build"
    build_dir.mkdir(parents=True)
    dll = build_dir / "havenbt_winrt.dll"
    dll.write_bytes(b"not a real dll, just a path to find")

    fake = _fake_lib()
    with patch("haven.integrations.bluetooth.native.ctypes.util.find_library", return_value=None), patch(
        "haven.integrations.bluetooth.native.platform.system", return_value="Windows"
    ), patch("haven.integrations.bluetooth.native._REPO_ROOT", tmp_path), patch(
        "haven.integrations.bluetooth.native.ctypes.CDLL", return_value=fake
    ) as cdll:
        CtypesBluetoothLibrary.load()

    cdll.assert_called_once_with(str(dll))


def test_find_dev_build_returns_none_when_nothing_was_built(tmp_path):
    with patch("haven.integrations.bluetooth.native.platform.system", return_value="Windows"), patch(
        "haven.integrations.bluetooth.native._REPO_ROOT", tmp_path
    ):
        assert _find_dev_build() is None


def test_find_dev_build_never_picks_the_fixture_backend(tmp_path):
    build_dir = tmp_path / "native" / "haven-bt" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "havenbt_fixture.dll").write_bytes(b"fixture, not real hardware")

    with patch("haven.integrations.bluetooth.native.platform.system", return_value="Windows"), patch(
        "haven.integrations.bluetooth.native._REPO_ROOT", tmp_path
    ):
        assert _find_dev_build() is None


def test_load_configures_prototypes_and_creates_a_context():
    fake = _fake_lib()
    with patch("haven.integrations.bluetooth.native.ctypes.util.find_library", return_value="havenbt"), patch(
        "haven.integrations.bluetooth.native.ctypes.CDLL", return_value=fake
    ):
        library = CtypesBluetoothLibrary.load()

    fake.hb_context_create.assert_called_once()
    assert fake.hb_gatt_write.argtypes is not None
    assert library is not None


def test_init_raises_when_context_create_returns_null():
    fake = _fake_lib()
    fake.hb_context_create.return_value = 0

    with pytest.raises(NativeBluetoothError):
        CtypesBluetoothLibrary(fake)


def test_scan_start_and_stop_delegate_to_the_library():
    fake = _fake_lib()
    fake.hb_scan_start.return_value = HbStatus.OK
    fake.hb_scan_stop.return_value = HbStatus.OK
    library = CtypesBluetoothLibrary(fake)

    assert library.scan_start() == HbStatus.OK
    assert library.scan_stop() == HbStatus.OK
    fake.hb_scan_start.assert_called_once_with(0x1234)
    fake.hb_scan_stop.assert_called_once_with(0x1234)


def test_gatt_write_marshals_bytes_and_length():
    fake = _fake_lib()
    fake.hb_gatt_write.return_value = HbStatus.OK
    library = CtypesBluetoothLibrary(fake)

    result = library.gatt_write(42, "0000fff1-0000-1000-8000-00805f9b34fb", b"\x01\x32")

    assert result == HbStatus.OK
    args = fake.hb_gatt_write.call_args.args
    assert args[0] == 0x1234
    assert args[1] == 42
    assert args[2] == b"0000fff1-0000-1000-8000-00805f9b34fb"
    assert bytes(args[3][:2]) == b"\x01\x32"
    assert args[4] == 2


def test_gatt_read_raises_on_non_ok_status():
    fake = _fake_lib()
    fake.hb_gatt_read.return_value = HbStatus.ERROR_NOT_CONNECTED
    library = CtypesBluetoothLibrary(fake)

    with pytest.raises(NativeBluetoothError):
        library.gatt_read(42, "0000fff1-0000-1000-8000-00805f9b34fb")


def test_poll_event_returns_none_when_queue_is_empty():
    fake = _fake_lib()
    fake.hb_poll_event.return_value = 0
    library = CtypesBluetoothLibrary(fake)

    assert library.poll_event() is None
