"""Check real native process startup using disposable, isolated state.

This catches XAML construction failures. It does not prove Core connection
or interactive workflows. It never changes the installed user's settings.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def check(native: Path, seconds: float = 3.0) -> dict:
    if os.name != "nt" or not native.is_file() or not 0.5 <= seconds <= 15:
        raise ValueError("Select an existing Windows native executable and a duration from 0.5 to 15 seconds.")
    native = native.resolve()
    root = Path(tempfile.mkdtemp(prefix="haven-native-startup-"))
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("HAVEN_", "PYTHON", "DOTNET_"))
           and not any(word in k.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL"))}
    for name in ("home", "desktop", "data", "models", "temp", "appdata", "localappdata"):
        (root / name).mkdir()
    env.update(HOME=str(root / "home"), USERPROFILE=str(root / "home"),
               APPDATA=str(root / "appdata"), LOCALAPPDATA=str(root / "localappdata"),
               TEMP=str(root / "temp"), TMP=str(root / "temp"),
               HAVEN_DATA_DIR=str(root / "data"), HAVEN_MODELS_ROOT=str(root / "models"),
               HAVEN_DESKTOP_DATA_DIR=str(root / "desktop"),
               USERNAME="synthetic-haven-owner", USER="synthetic-haven-owner", LOGNAME="synthetic-haven-owner")
    process = None
    try:
        with (root / "stdout.log").open("w") as out, (root / "stderr.log").open("w") as err:
            process = subprocess.Popen([str(native)], cwd=native.parent, env=env, stdout=out, stderr=err)
        deadline = time.monotonic() + seconds
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        diagnostic = root / "desktop/native-diagnostics.log"
        unhandled = diagnostic.is_file() and "UNHANDLED" in diagnostic.read_text(encoding="utf-8")
        result = {"ok": process.poll() is None and not unhandled,
                  "check": "native XAML process startup only; Core intentionally absent",
                  "early_exit_code": process.poll(), "unhandled_exception": unhandled,
                  "isolated_root": str(root), "native_executable": str(native)}
        (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        if process is not None and process.poll() is None:
            process.terminate()  # Only this disposable test process.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-exe", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=3)
    args = parser.parse_args()
    result = check(args.native_exe, args.seconds)
    print(json.dumps(result))
    raise SystemExit(0 if result["ok"] else 1)
