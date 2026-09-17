"""Real end-to-end proof against a compiled HAVEN-BT library.

Every other Bluetooth test in this repo mocks the ctypes boundary
(`test_bluetooth_native_abi.py`) or uses a plain-Python fake
(`test_bluetooth_provider.py`), because no compiler was available when they
were written. This file is different: it compiles
`native/haven-bt/src/fixture/fixture_backend.c` -- a real, deterministic
implementation of `haven_bt.h`, not a platform backend -- and runs
`CtypesBluetoothLibrary` and `BluetoothProvider` against the actual
resulting `.dll`/`.so`. If no C compiler is on PATH, every test here is
skipped rather than failed: this is a bonus proof that the ABI and its
Python binding actually work together, not a requirement to run the suite.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.core.domain import DeviceCommand
from haven.devices import CapabilityDescriptor, ControlClass
from haven.discovery import enroll_device
from haven.integrations.bluetooth import BluetoothProvider
from haven.integrations.bluetooth.native import CtypesBluetoothLibrary, HbEventType

NATIVE_ROOT = Path(__file__).resolve().parent.parent / "native" / "haven-bt"
CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"


def _find_compiler() -> str | None:
    for candidate in ("x86_64-w64-mingw32-clang", "clang", "cc", "gcc"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _compile_fixture_backend(tmp_path: Path, compiler: str) -> Path:
    suffix = {"Windows": ".dll", "Darwin": ".dylib"}.get(platform.system(), ".so")
    output = tmp_path / f"havenbt_fixture{suffix}"
    subprocess.run(
        [
            compiler, "-Wall", "-Wextra",
            "-I", str(NATIVE_ROOT / "include"),
            "-shared", "-o", str(output),
            str(NATIVE_ROOT / "src" / "fixture" / "fixture_backend.c"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output


@pytest.fixture(scope="module")
def fixture_library_path():
    """A self-contained temp dir, not pytest's shared base-temp.

    Deliberately not the built-in `tmp_path`/`tmp_path_factory` fixtures:
    those share one base directory across the whole test run, and this
    file should not depend on that directory's permissions being usable --
    only on being able to make its own directory and clean it up.
    """

    compiler = _find_compiler()
    if compiler is None:
        pytest.skip("no C compiler on PATH; this file proves compiled-library behavior, it does not require it")
    with tempfile.TemporaryDirectory(prefix="havenbt-") as tmp_dir:
        try:
            yield _compile_fixture_backend(Path(tmp_dir), compiler)
        except subprocess.CalledProcessError as exc:
            pytest.fail(f"fixture_backend.c failed to compile with {compiler}:\n{exc.stderr}")


def test_the_header_and_fixture_backend_actually_compile(fixture_library_path: Path):
    assert fixture_library_path.exists()


def test_scan_reports_the_fixture_device_through_real_ctypes_calls(fixture_library_path: Path):
    library = CtypesBluetoothLibrary.load(path=str(fixture_library_path))
    try:
        assert library.scan_start() == 0
        event = library.poll_event()
        assert event is not None
        assert event.type == HbEventType.DEVICE_FOUND
        assert event.device == 1
        assert event.rssi == -42
        assert event.name == b"Fixture BLE Light"
        assert library.poll_event() is None  # queue drained
        assert library.scan_stop() == 0
    finally:
        library.close()


def test_gatt_write_then_read_round_trips_through_real_native_state(fixture_library_path: Path):
    library = CtypesBluetoothLibrary.load(path=str(fixture_library_path))
    try:
        assert library.gatt_write(1, CHARACTERISTIC_UUID, b"\x01\x32") == 0
        assert library.gatt_read(1, CHARACTERISTIC_UUID) == b"\x01\x32"
    finally:
        library.close()


def test_full_pipeline_discover_enroll_execute_against_a_real_compiled_library(fixture_library_path: Path):
    library = CtypesBluetoothLibrary.load(path=str(fixture_library_path))
    try:
        provider = BluetoothProvider(library)

        (candidate,) = provider.discover()
        assert candidate.candidate_id == "1"
        assert candidate.signal_strength == -42.0

        manifest = enroll_device(
            candidate,
            device_type="light",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service=CHARACTERISTIC_UUID
                ),
            ),
            approved_by="owner-1",
            justification="Real end-to-end proof against the compiled fixture backend.",
        )

        command = DeviceCommand(
            request_id="request-1",
            target_device_id=manifest.device_id,
            service=CHARACTERISTIC_UUID,
            parameters=(("bytes", b"\x01"),),
            requested_at=datetime.now(timezone.utc),
        )
        result = provider.execute(command)

        assert result.success is True
        assert library.gatt_read(1, CHARACTERISTIC_UUID) == b"\x01"
    finally:
        library.close()
