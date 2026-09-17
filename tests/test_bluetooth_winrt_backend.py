"""WinRT backend: real compile + link + load, never live scan results.

This proves `native/haven-bt/src/platform/windows/winrt_backend.cpp` --
Windows.Devices.Bluetooth via real WinRT APIs, not a fixture -- still
compiles, links against WindowsApp.lib, and loads through
CtypesBluetoothLibrary as the codebase evolves. It deliberately never
asserts anything about *scan results*: whether the Bluetooth radio is on,
what devices are nearby, and what they advertise are facts about the host
machine at a moment in time, not about this code, and asserting on them
would make CI flaky for reasons with nothing to do with a real regression.

Skipped entirely off Windows, or wherever MSVC + the Windows SDK's C++/WinRT
headers are not installed (`Microsoft.VisualStudio.Component.VC.Tools.x86.x64`).
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
from pathlib import Path

import pytest

from haven.integrations.bluetooth.native import CtypesBluetoothLibrary

NATIVE_ROOT = Path(__file__).resolve().parent.parent / "native" / "haven-bt"
VSWHERE = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")


def _msvc_available() -> bool:
    return platform.system() == "Windows" and VSWHERE.exists()


def _compile_winrt_backend(tmp_path: Path) -> Path:
    output = tmp_path / "havenbt_winrt.dll"
    result = subprocess.run(
        [str(NATIVE_ROOT / "build_windows.cmd"), str(output)],
        cwd=str(NATIVE_ROOT),
        capture_output=True,
        text=True,
        shell=True,
    )
    if result.returncode != 0 or not output.exists():
        pytest.fail(f"winrt_backend.cpp failed to build:\n{result.stdout}\n{result.stderr}")
    return output


@pytest.fixture(scope="module")
def winrt_library_path():
    if not _msvc_available():
        pytest.skip("no MSVC + Windows SDK found; this file proves the real WinRT backend builds, it does not require it")
    with tempfile.TemporaryDirectory(prefix="havenbt-winrt-") as tmp_dir:
        yield _compile_winrt_backend(Path(tmp_dir))


def test_the_winrt_backend_compiles_and_links(winrt_library_path: Path):
    assert winrt_library_path.exists()


def test_a_context_can_be_created_and_destroyed_against_the_real_dll(winrt_library_path: Path):
    library = CtypesBluetoothLibrary.load(path=str(winrt_library_path))
    library.close()  # must not raise -- exercises the real FreeLibrary path too
