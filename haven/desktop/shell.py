"""Windows desktop host for HAVEN's compatibility and native clients.

This is intentionally a host, not a second UI implementation.  The shell:

* starts the Python HAVEN service graph;
* uses a local named pipe for the native WinUI client when ``--native`` is
  selected; or
* gives the compatibility renderer a fresh HTTP-only session cookie and opens
  it in Edge app mode.

The native path does not expose the compatibility HTTP socket.  The Edge path
remains available during the native UI parity migration without creating a
second authority or provider runtime.
"""

from __future__ import annotations

import argparse
import base64
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
from threading import Lock, Thread
from typing import Callable
from urllib.parse import quote

from haven.web.server import make_server
from haven.web.setup_config import default_data_dir
from haven.ipc.named_pipe import installation_id_for_data_dir, installation_pipe_name

from .folder_picker import choose_folder
from .instance_lock import InstanceAlreadyRunning, InstanceLock
from .native_host import NativeIpcHost


class DesktopShellError(RuntimeError):
    """The native host could not be started."""


class DesktopShellAlreadyRunning(DesktopShellError):
    """A different HAVEN host already owns this installation."""


_ACTIVATION_FILE = ".haven-desktop-control.json"


def _activation_path(data_dir: Path) -> Path:
    return data_dir / _ACTIVATION_FILE


def _protect_activation_token(token: str) -> str | None:
    """Protect a token with the current Windows user's DPAPI profile."""

    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    raw = token.encode("utf-8")
    source = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        ctypes.c_wchar_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "HAVEN desktop activation",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "Windows DPAPI could not protect the HAVEN activation token")
    try:
        protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
    return base64.urlsafe_b64encode(protected).decode("ascii")


def _unprotect_activation_token(value: str) -> str | None:
    """Unprotect a Windows DPAPI activation token, or return None."""

    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeError):
        return None
    source = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    description_pointer = ctypes.POINTER(ctypes.c_wchar_p)
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        description_pointer,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        return None
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _write_activation_record(path: Path, *, port: int, token: str) -> None:
    """Publish the resident shell endpoint atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        protected = _protect_activation_token(token)
        if os.name == "nt" and protected is None:
            raise OSError("Windows DPAPI is unavailable; refusing to persist an activation token")
        token_payload = {"token_protected": protected} if protected is not None else {"token": token}
        temporary.write_text(
            json.dumps({"port": int(port), **token_payload}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows ACLs are inherited from the private data directory;
            # chmod is still useful for Unix hosts without changing startup
            # behavior when the platform does not expose POSIX modes.
            pass
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
    protected = payload.get("token_protected")
    if isinstance(protected, str) and protected:
        try:
            token = _unprotect_activation_token(protected)
        except (OSError, TypeError, ValueError):
            # A damaged or unavailable user profile must make activation
            # unavailable, never fall back to treating ciphertext as a token.
            return None
    else:
        token = payload.get("token")
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
        return (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("activated") is True
        )
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
    """Restore and foreground a visible top-level window for these PIDs.

    Windows normally prevents a background process from stealing focus.  The
    thread-input attachment below is the documented workaround for a user
    initiated local activation request.  If foregrounding is still denied,
    flashing the matching taskbar window is the honest fallback: the caller
    gets attention without HAVEN claiming that focus changed.
    """

    if os.name != "nt" or not process_ids:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.restype = wintypes.HWND
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.FlashWindowEx.restype = wintypes.BOOL
    activated = False
    attention_requested = False

    class _FlashWindowInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    user32.FlashWindowEx.argtypes = [ctypes.POINTER(_FlashWindowInfo)]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        nonlocal activated, attention_requested
        owner = wintypes.DWORD()
        target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value not in process_ids or not user32.IsWindowVisible(hwnd):
            return True
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        current_thread = user32.GetCurrentThreadId()
        attached = False
        if target_thread and target_thread != current_thread:
            attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetForegroundWindow(hwnd)
            activated = user32.GetForegroundWindow() == hwnd
            if not activated:
                flash = _FlashWindowInfo(
                    ctypes.sizeof(_FlashWindowInfo),
                    hwnd,
                    0x00000002 | 0x0000000C,  # FLASHW_TRAY | FLASHW_TIMERNOFG
                    3,
                    0,
                )
                user32.FlashWindowEx(ctypes.byref(flash))
                attention_requested = True
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
        return not activated and not attention_requested

    user32.EnumWindows(visit, 0)
    # A taskbar flash is useful, but it is not the same thing as foreground
    # focus.  Keep the boolean truthful for the IPC response.
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


def _native_process_ids(pipe_name: str) -> tuple[int, ...]:
    """Find native client processes attached to one installation pipe."""

    if os.name != "nt":
        return ()
    marker = pipe_name.replace("'", "''")
    script = (
        "$pipe = '" + marker + "'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'Haven.Desktop.exe' -and $_.CommandLine "
        "-and $_.CommandLine.Contains($pipe) } | "
        "Select-Object -ExpandProperty ProcessId"
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


def _activate_native_existing_window(data_dir: Path) -> bool:
    pipe_name = installation_pipe_name(installation_id_for_data_dir(data_dir))
    return _activate_process_windows(set(_native_process_ids(pipe_name)))


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
    """Own one HAVEN service graph and one optional desktop client."""

    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        demo: bool = False,
        edge_path: str | Path | None = None,
        port: int = 0,
        background: bool = False,
        native: bool = False,
        native_path: str | Path | None = None,
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
        self.native = native
        self.native_path = Path(native_path) if native_path is not None else None
        self._server_factory = server_factory
        self._process_factory = process_factory
        self._folder_picker = folder_picker
        self._window_activator = window_activator
        self.server = None
        self.native_ipc: NativeIpcHost | None = None
        self.server_thread: Thread | None = None
        self.process = None
        self.session_token: str | None = None
        self.bootstrap_token: str | None = None
        self.activation_token: str | None = None
        self.bootstrap_url: str | None = None
        self._edge_profile = None
        self._window_lock = Lock()
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

    def _resolve_native(self) -> Path:
        native = self.native_path
        if native is None:
            configured = os.environ.get("HAVEN_NATIVE_CLIENT")
            if configured:
                native = Path(configured)
            else:
                native = (
                    Path(__file__).resolve().parents[2]
                    / "native"
                    / "Haven.Desktop"
                    / "bin"
                    / "x64"
                    / "Debug"
                    / "net8.0-windows10.0.19041.0"
                    / "Haven.Desktop.exe"
                )
        if not native.is_file():
            raise DesktopShellError(
                "HAVEN Native Desktop is not built; build native/Haven.Desktop "
                "or set HAVEN_NATIVE_CLIENT"
            )
        return native

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

    def _window_is_alive(self) -> bool:
        process = self.process
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:
            # A process-like test/host seam without `poll` is safer to treat
            # as alive than to launch a second window against the same shell.
            return True

    def _discard_dead_window_locked(self) -> None:
        """Drop a window process that the user already closed.

        This runs only while the shell's lifecycle lock is held. The resident
        server and installation lock remain alive; only the window resources
        are discarded so the next activation can create a fresh one.
        """

        process = self.process
        profile = self._edge_profile
        if process is None or self._window_is_alive():
            return
        self.process = None
        self._edge_profile = None
        self.bootstrap_url = None
        if profile is not None:
            try:
                profile.cleanup()
            except Exception:
                pass

    def _open_window_locked(self) -> bool:
        """Create the Edge app window without changing resident-core state."""

        if self.server is None:
            return False
        if self._window_is_alive():
            return True
        self._discard_dead_window_locked()
        edge = self._resolve_edge()
        # Bootstrap URLs are one-shot capabilities. The resident server
        # survives when an Edge window closes, so rotate the URL nonce before
        # every new window while keeping the long-lived session cookie secret
        # unchanged.
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.server.rotate_bootstrap_token(self.bootstrap_token)
        profile = tempfile.TemporaryDirectory(prefix="haven-desktop-edge-")
        port = self.server.server_address[1]
        bootstrap_url = (
            f"http://127.0.0.1:{port}/__desktop_bootstrap?session="
            f"{quote(self.bootstrap_token, safe='')}"
        )
        try:
            process = self._process_factory(
                [
                    str(edge),
                    f"--app={bootstrap_url}",
                    f"--user-data-dir={profile.name}",
                    "--new-window",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]
            )
        except Exception:
            profile.cleanup()
            raise
        self._edge_profile = profile
        self.process = process
        self.bootstrap_url = bootstrap_url
        return True

    def _open_window(self) -> bool:
        with self._window_lock:
            return self._open_window_locked()

    def _open_native_window(self) -> bool:
        if self.native_ipc is None:
            return False
        native = self._resolve_native()
        if self._window_is_alive():
            return True
        self._discard_dead_window_locked()
        # Do not put the IPC bearer token in the child command line.  On
        # Windows, same-user processes can inspect command lines without
        # needing the Core's named-pipe connection.  An inherited anonymous
        # pipe carries the token once, then closes; the native client supports
        # this handoff through `--ipc-token-stdin`.
        process = self._process_factory(
            [
                str(native),
                "--pipe-name",
                self.native_ipc.pipe_name,
                "--ipc-token-stdin",
            ],
            stdin=subprocess.PIPE,
        )
        self.process = process
        token_stream = getattr(process, "stdin", None)
        if token_stream is None:
            raise DesktopShellError("native client did not expose an IPC token handoff pipe")
        try:
            token_stream.write((self.native_ipc.auth_token + "\n").encode("utf-8"))
            token_stream.flush()
        finally:
            token_stream.close()
        return True

    def _activate_existing_window(self) -> bool:
        if self._window_activator is not None:
            try:
                return bool(self._window_activator())
            except Exception:
                return False
        with self._window_lock:
            if not self._window_is_alive():
                return self._open_native_window() if self.native else self._open_window_locked()
            if self.native:
                # The native client owns its own activation/focus bridge. The
                # resident process is already alive, so activation is safely
                # treated as handled until that bridge is added.
                return True
            if self._edge_profile is None:
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
            self.session_token = secrets.token_urlsafe(32)
            self.bootstrap_token = secrets.token_urlsafe(32)
            self.activation_token = secrets.token_urlsafe(32)
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
            if self.native:
                self.native_ipc = NativeIpcHost(server=self.server, data_dir=self.data_dir).start()
            if self.native:
                # Native mode talks to the same service graph through the
                # named pipe.  Do not leave the compatibility HTTP socket
                # listening just because the transitional composition object
                # is still a HavenWebServer instance.
                self.server.server_close()
            else:
                self.server_thread = Thread(
                    target=self.server.serve_forever,
                    name="haven-desktop-server",
                    daemon=True,
                )
                self.server_thread.start()
                self._publish_activation_record()
            if self.native:
                if not self.background:
                    with self._window_lock:
                        self._open_native_window()
            elif not self.background:
                self._open_window()
        except InstanceAlreadyRunning as exc:
            self._instance_lock = None
            activated = (
                _activate_native_existing_window(self.data_dir)
                if self.native
                else _request_activation(self.data_dir)
            )
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
            if self.native:
                if self.native_ipc is None:
                    raise DesktopShellError("HAVEN Native Desktop is not running")
                try:
                    while self.native_ipc.is_running:
                        time.sleep(1)
                    return 0
                finally:
                    self.close()
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
        with self._window_lock:
            process = self.process
            self.process = None
            edge_profile = self._edge_profile
            self._edge_profile = None
            self.bootstrap_url = None
            server = self.server
            self.server = None

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
        if edge_profile is not None:
            _terminate_edge_profile(Path(edge_profile.name))

        if server is not None:
            native_ipc = self.native_ipc
            self.native_ipc = None
            if native_ipc is not None:
                native_ipc.stop()
            thread = self.server_thread
            if thread is not None:
                try:
                    server.shutdown()
                except Exception:
                    pass
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
        "--native",
        action="store_true",
        help="use the compiled WinUI client instead of the compatibility Edge host",
    )
    parser.add_argument(
        "--native-path",
        default=None,
        help="path to Haven.Desktop.exe (defaults to the repository Debug build)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="loopback port (default: ephemeral in windowed mode, 8080 in background mode)",
    )
    args = parser.parse_args(argv)
    port = args.port if args.port is not None else (8080 if args.background else 0)
    shell = DesktopShell(
        data_dir=args.data_dir,
        demo=args.demo,
        port=port,
        background=args.background,
        native=args.native,
        native_path=args.native_path,
    )
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
