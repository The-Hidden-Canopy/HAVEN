"""Windows desktop wrapper for the existing HAVEN local renderer.

This is intentionally a host, not a second UI implementation.  The shell:

* starts the loopback-only HAVEN server on an ephemeral port;
* gives that launch a fresh, HTTP-only session cookie;
* opens the existing renderer in Edge app mode; and
* owns shutdown when the app window exits.

Edge app mode is the zero-dependency first shell for this repository.  It
uses the installed WebView-capable browser without making the project depend
on a Python GUI framework or a native UI rewrite.  A later WebView2 host can
replace only this module while keeping the server and renderer contracts.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Thread
from typing import Callable
from urllib.parse import quote

from haven.web.server import make_server

from .folder_picker import choose_folder


class DesktopShellError(RuntimeError):
    """The native host could not be started."""


def _edge_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.environ.get("HAVEN_EDGE_PATH")
    if configured:
        candidates.append(Path(configured))
    for command in ("msedge.exe", "msedge"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    for variable in ("ProgramFiles", "LOCALAPPDATA", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def find_edge_executable() -> Path | None:
    """Return an installed Edge executable, or ``None`` without guessing."""

    for candidate in _edge_candidates():
        if candidate.is_file():
            return candidate
    return None


def _edge_profile_process_ids(profile: Path) -> tuple[int, ...]:
    """Find Edge processes belonging to one generated launch profile."""

    if os.name != "nt":
        return ()
    profile_text = str(profile).replace("'", "''")
    script = (
        "$profile = '" + profile_text + "'; "
        'Get-CimInstance Win32_Process -Filter "Name = \'msedge.exe\'" '
        "| Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profile) } "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    process_ids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            process_id = int(line.strip())
        except ValueError:
            continue
        if process_id > 0:
            process_ids.append(process_id)
    return tuple(dict.fromkeys(process_ids))


def _terminate_edge_profile(profile: Path) -> None:
    """Stop only Edge children carrying this shell's generated profile."""

    process_ids = _edge_profile_process_ids(profile)
    if not process_ids:
        return
    for process_id in process_ids:
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _edge_profile_process_ids(profile):
        time.sleep(0.1)


class DesktopShell:
    """Own one HAVEN server and one native app-mode browser process."""

    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        demo: bool = False,
        edge_path: str | Path | None = None,
        server_factory: Callable = make_server,
        process_factory: Callable = subprocess.Popen,
        folder_picker: Callable[[], str | None] = choose_folder,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.demo = demo
        self.edge_path = Path(edge_path) if edge_path is not None else None
        self._server_factory = server_factory
        self._process_factory = process_factory
        self._folder_picker = folder_picker
        self.server = None
        self.server_thread: Thread | None = None
        self.process = None
        self.session_token: str | None = None
        self.bootstrap_url: str | None = None
        self._edge_profile = None

    def _resolve_edge(self) -> Path:
        edge = self.edge_path or find_edge_executable()
        if edge is None or not edge.is_file():
            raise DesktopShellError(
                "HAVEN Desktop needs Microsoft Edge (or set HAVEN_EDGE_PATH); "
                "the existing WebUI remains available with python -m haven.web.server"
            )
        return edge

    def start(self) -> "DesktopShell":
        if self.server is not None or self.process is not None:
            return self
        edge = self._resolve_edge()
        self.session_token = secrets.token_urlsafe(32)
        try:
            # Edge otherwise hands `--app` off to an already-running browser
            # profile and the child process exits immediately.  A per-launch
            # profile keeps this desktop shell's lifetime tied to its window
            # and avoids reusing the user's normal browser state.
            self._edge_profile = tempfile.TemporaryDirectory(prefix="haven-desktop-edge-")
            self.server, _ = self._server_factory(
                0,
                data_dir=self.data_dir,
                demo=self.demo,
                session_token=self.session_token,
                folder_picker=self._folder_picker,
            )
            self.server_thread = Thread(
                target=self.server.serve_forever,
                name="haven-desktop-server",
                daemon=True,
            )
            self.server_thread.start()
            port = self.server.server_address[1]
            self.bootstrap_url = (
                f"http://127.0.0.1:{port}/__desktop_bootstrap?session="
                f"{quote(self.session_token, safe='')}"
            )
            self.process = self._process_factory(
                [
                    str(edge),
                    f"--app={self.bootstrap_url}",
                    f"--user-data-dir={self._edge_profile.name}",
                    "--new-window",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]
            )
        except Exception as exc:
            self.close()
            if isinstance(exc, DesktopShellError):
                raise
            raise DesktopShellError(f"could not start HAVEN Desktop: {exc}") from exc
        return self

    def wait(self) -> int:
        if self.process is None:
            raise DesktopShellError("HAVEN Desktop is not running")
        process = self.process
        profile = self._edge_profile
        try:
            return_code = int(process.wait())
            if profile is not None and os.name == "nt":
                profile_path = Path(profile.name)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if _edge_profile_process_ids(profile_path):
                        break
                    time.sleep(0.1)
                while _edge_profile_process_ids(profile_path):
                    time.sleep(0.25)
            return return_code
        finally:
            self.close()

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

        edge_profile = self._edge_profile
        self._edge_profile = None
        if edge_profile is not None:
            _terminate_edge_profile(Path(edge_profile.name))

        server = self.server
        self.server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            thread = self.server_thread
            if thread is not None:
                thread.join(timeout=5)
            try:
                server.server_close()
            except Exception:
                pass
        self.server_thread = None
        if edge_profile is not None:
            try:
                edge_profile.cleanup()
            except Exception:
                # An interrupted Edge shutdown can briefly retain a profile
                # lock.  Leaving this generated temp directory is safer than
                # deleting anything outside the shell's own launch scope.
                pass

    def run(self) -> int:
        self.start()
        return self.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HAVEN in a native desktop window.")
    parser.add_argument("--data-dir", default=None, help="HAVEN data directory")
    parser.add_argument("--demo", action="store_true", help="force the explicit demo household")
    args = parser.parse_args(argv)
    shell = DesktopShell(data_dir=args.data_dir, demo=args.demo)
    try:
        return shell.run()
    except KeyboardInterrupt:
        shell.close()
        return 130
    except DesktopShellError as exc:
        print(f"HAVEN Desktop: {exc}", file=sys.stderr)
        return 2


__all__ = ["DesktopShell", "DesktopShellError", "find_edge_executable", "main"]
