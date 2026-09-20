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
import http.client
import json
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
from haven.web.setup_config import default_data_dir

from .folder_picker import choose_folder
from .instance_lock import InstanceAlreadyRunning, InstanceLock


class DesktopShellError(RuntimeError):
    """The native host could not be started."""


class DesktopShellAlreadyRunning(DesktopShellError):
    """A different HAVEN host already owns this installation."""


_ACTIVATION_FILE = ".haven-desktop-control.json"


def _activation_path(data_dir: Path) -> Path:
    return data_dir / _ACTIVATION_FILE


def _write_activation_record(path: Path, *, port: int, token: str) -> None:
    """Publish the resident shell endpoint atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps({"port": int(port), "token": token}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_activation_record(path: Path) -> tuple[int, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    port = payload.get("port")
    token = payload.get("token")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    if not isinstance(token, str) or not token:
        return None
    return port, token


def _request_activation(data_dir: Path) -> bool:
    """Ask the owner of an already-held installation lock to activate."""

    record = _read_activation_record(_activation_path(data_dir))
    if record is None:
        return False
    port, token = record
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        body = json.dumps({"token": token}).encode("utf-8")
        connection.request(
            "POST",
            "/__desktop_activate",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response_body = response.read()
        if response.status != 200:
            return False
        try:
            payload = json.loads(response_body)
        except (ValueError, TypeError):
            return False
        return isinstance(payload, dict) and payload.get("ok") is True
    except (OSError, ValueError):
        return False
    finally:
        connection.close()


def _remove_activation_record(data_dir: Path, *, token: str | None) -> None:
    """Remove only the control record owned by this shell."""

    if not token:
        return
    path = _activation_path(data_dir)
    record = _read_activation_record(path)
    if record is None or record[1] != token:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _activate_process_windows(process_ids: set[int]) -> bool:
    """Restore and foreground a visible top-level window for these PIDs."""

    if os.name != "nt" or not process_ids:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    activated = False

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        nonlocal activated
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value not in process_ids or not user32.IsWindowVisible(hwnd):
            return True
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        activated = True
        return False

    user32.EnumWindows(visit, 0)
    return activated


def _resolved_data_dir(data_dir: str | Path | None) -> Path:
    """Resolve the same data-dir precedence used by the web server."""

    configured = os.environ.get("HAVEN_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if data_dir is not None:
        return Path(data_dir).expanduser().resolve()
    return default_data_dir().resolve()


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
    """Own one HAVEN server and, when requested, one app-mode browser."""

    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        demo: bool = False,
        edge_path: str | Path | None = None,
        port: int = 0,
        background: bool = False,
        server_factory: Callable = make_server,
        process_factory: Callable = subprocess.Popen,
        folder_picker: Callable[[], str | None] = choose_folder,
        window_activator: Callable[[], bool] | None = None,
    ) -> None:
        self.data_dir = _resolved_data_dir(data_dir)
        self.demo = demo
        self.edge_path = Path(edge_path) if edge_path is not None else None
        self.port = port
        self.background = background
        self._server_factory = server_factory
        self._process_factory = process_factory
        self._folder_picker = folder_picker
        self._window_activator = window_activator
        self.server = None
        self.server_thread: Thread | None = None
        self.process = None
        self.session_token: str | None = None
        self.bootstrap_token: str | None = None
        self.activation_token: str | None = None
        self.bootstrap_url: str | None = None
        self._edge_profile = None
        self._instance_lock: InstanceLock | None = None
        self._reserved_instance_lock: InstanceLock | None = None

    def _resolve_edge(self) -> Path:
        edge = self.edge_path or find_edge_executable()
        if edge is None or not edge.is_file():
            raise DesktopShellError(
                "HAVEN Desktop needs Microsoft Edge (or set HAVEN_EDGE_PATH); "
                "the existing WebUI remains available with python -m haven.web.server"
            )
        return edge

    def _before_data_dir_changed(self, data_dir: Path) -> None:
        """Reserve a target installation lock before setup moves anything."""

        target = Path(data_dir).expanduser().resolve()
        if self._instance_lock is None or self._instance_lock.data_dir == target:
            return
        if self._reserved_instance_lock is not None:
            if self._reserved_instance_lock.data_dir == target:
                return
            self._reserved_instance_lock.release()
            self._reserved_instance_lock = None
        self._reserved_instance_lock = InstanceLock(target).acquire()

    def _on_data_dir_changed(self, data_dir: Path) -> None:
        """Commit the already-reserved lock after setup moved successfully."""

        target = Path(data_dir).expanduser().resolve()
        if self._instance_lock is None or self._instance_lock.data_dir == target:
            return
        replacement = self._reserved_instance_lock
        if replacement is None:
            # Keep the callback safe for non-desktop callers that may still
            # invoke it directly, while the normal DesktopShell path always
            # reserves before setup starts moving files.
            replacement = InstanceLock(target).acquire()
        elif replacement.data_dir != target:
            replacement.release()
            self._reserved_instance_lock = None
            raise DesktopShellError("reserved HAVEN instance lock does not match the new data directory")
        self._reserved_instance_lock = None
        previous_data_dir = self.data_dir
        previous = self._instance_lock
        self._instance_lock = replacement
        self.data_dir = target
        if self.server is not None and previous_data_dir != target:
            try:
                self._publish_activation_record()
            except OSError:
                # The live server and lock are already valid.  Keep the old
                # record only if publishing the new one failed; it is safer
                # than deleting the only activation route during a move.
                pass
            else:
                _remove_activation_record(previous_data_dir, token=self.activation_token)
        previous.release()

    def _on_data_dir_change_failed(self) -> None:
        """Release a reservation when setup aborts before committing it."""

        reservation = self._reserved_instance_lock
        self._reserved_instance_lock = None
        if reservation is not None:
            reservation.release()

    def _activate_existing_window(self) -> bool:
        if self._window_activator is not None:
            try:
                return bool(self._window_activator())
            except Exception:
                return False
        if self.background or self._edge_profile is None:
            return False
        process_ids = set(_edge_profile_process_ids(Path(self._edge_profile.name)))
        process_id = getattr(self.process, "pid", None)
        if isinstance(process_id, int) and process_id > 0:
            process_ids.add(process_id)
        return _activate_process_windows(process_ids)

    def _publish_activation_record(self) -> None:
        if self.server is None or self.activation_token is None:
            return
        _write_activation_record(
            _activation_path(self.data_dir),
            port=self.server.server_address[1],
            token=self.activation_token,
        )

    def start(self) -> "DesktopShell":
        if self.server is not None or self.process is not None:
            return self
        try:
            self._instance_lock = InstanceLock(self.data_dir).acquire()
            edge = None if self.background else self._resolve_edge()
            self.session_token = secrets.token_urlsafe(32)
            self.bootstrap_token = secrets.token_urlsafe(32)
            self.activation_token = secrets.token_urlsafe(32)
            # Edge otherwise hands `--app` off to an already-running browser
            # profile and the child process exits immediately.  A per-launch
            # profile keeps this desktop shell's lifetime tied to its window
            # and avoids reusing the user's normal browser state.
            if not self.background:
                self._edge_profile = tempfile.TemporaryDirectory(prefix="haven-desktop-edge-")
            self.server, _ = self._server_factory(
                self.port,
                data_dir=self.data_dir,
                demo=self.demo,
                session_token=self.session_token,
                bootstrap_token=self.bootstrap_token,
                activation_token=self.activation_token,
                on_activate=self._activate_existing_window,
                folder_picker=self._folder_picker,
                before_data_dir_changed=self._before_data_dir_changed,
                on_data_dir_changed=self._on_data_dir_changed,
                on_data_dir_change_failed=self._on_data_dir_change_failed,
            )
            self.server_thread = Thread(
                target=self.server.serve_forever,
                name="haven-desktop-server",
                daemon=True,
            )
            self.server_thread.start()
            port = self.server.server_address[1]
            self._publish_activation_record()
            if not self.background:
                self.bootstrap_url = (
                    f"http://127.0.0.1:{port}/__desktop_bootstrap?session="
                    f"{quote(self.bootstrap_token, safe='')}"
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
        except InstanceAlreadyRunning as exc:
            self._instance_lock = None
            activated = _request_activation(self.data_dir)
            detail = "; activation requested" if activated else ""
            raise DesktopShellAlreadyRunning(f"{exc}{detail}") from exc
        except Exception as exc:
            self.close()
            if isinstance(exc, DesktopShellError):
                raise
            raise DesktopShellError(f"could not start HAVEN Desktop: {exc}") from exc
        return self

    def wait(self) -> int:
        if self.background:
            if self.server is None or self.server_thread is None:
                raise DesktopShellError("HAVEN Desktop is not running")
            try:
                while self.server_thread.is_alive():
                    self.server_thread.join(timeout=1)
                return 0
            finally:
                self.close()
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
        _remove_activation_record(self.data_dir, token=self.activation_token)
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
        lock = self._instance_lock
        self._instance_lock = None
        if lock is not None:
            lock.release()
        self._on_data_dir_change_failed()
        self.session_token = None
        self.bootstrap_token = None
        self.activation_token = None

    def run(self) -> int:
        self.start()
        return self.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HAVEN in a native desktop window.")
    parser.add_argument("--data-dir", default=None, help="HAVEN data directory")
    parser.add_argument("--demo", action="store_true", help="force the explicit demo household")
    parser.add_argument(
        "--background",
        action="store_true",
        help="run the resident local host without opening a window",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="loopback port (default: ephemeral in windowed mode, 8080 in background mode)",
    )
    args = parser.parse_args(argv)
    port = args.port if args.port is not None else (8080 if args.background else 0)
    shell = DesktopShell(data_dir=args.data_dir, demo=args.demo, port=port, background=args.background)
    try:
        return shell.run()
    except KeyboardInterrupt:
        shell.close()
        return 130
    except DesktopShellAlreadyRunning:
        return 0
    except DesktopShellError as exc:
        print(f"HAVEN Desktop: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "DesktopShell",
    "DesktopShellAlreadyRunning",
    "DesktopShellError",
    "find_edge_executable",
    "main",
]
