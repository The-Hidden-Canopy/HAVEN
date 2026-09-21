"""Launch the built WinUI client against a temporary real HAVEN Core pipe."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from haven.desktop.shell import DesktopShell


def main() -> int:
    native = (
        Path(__file__).resolve().parents[1]
        / "native"
        / "Haven.Desktop"
        / "bin"
        / "x64"
        / "Debug"
        / "net8.0-windows10.0.19041.0"
        / "Haven.Desktop.exe"
    )
    if not native.is_file():
        raise SystemExit(f"native client is not built: {native}")
    with tempfile.TemporaryDirectory(prefix="haven-native-smoke-") as tmp:
        shell = DesktopShell(data_dir=Path(tmp) / "data", native=True, native_path=native, port=0)
        shell.start()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if shell.native_ipc is not None and shell.native_ipc.authenticated_once:
                    print("NATIVE SMOKE OK -- WinUI client authenticated to the Core pipe")
                    return 0
                time.sleep(0.1)
            return_code = shell.process.poll() if shell.process is not None else None
            raise SystemExit(
                f"native client launched but did not authenticate to the Core pipe "
                f"(return_code={return_code})"
            )
        finally:
            shell.close()


if __name__ == "__main__":
    raise SystemExit(main())
