"""Windows named-pipe transport for the native HAVEN IPC contract.

This is intentionally a small ctypes binding rather than a third-party
dependency.  The pipe rejects remote clients, uses bounded length-prefixed
frames, and requires an application-level authentication request before the
dispatcher sees any other method.

The transport owns no HAVEN state.  Callers provide a request handler that
must route mutations through the existing application/authority services.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import re
import threading
from collections.abc import Callable, Mapping
from ctypes import wintypes
from struct import Struct
from typing import Any

from .protocol import (
    MAX_FRAME_BYTES,
    IpcProtocolError,
    decode_frame,
    encode_frame,
    response_message,
)

_HEADER = Struct("<I")
_PIPE_ACCESS_DUPLEX = 0x00000003
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_ERROR_BROKEN_PIPE = 109
_ERROR_NO_DATA = 232
_ERROR_PIPE_CONNECTED = 535
_ERROR_OPERATION_ABORTED = 995
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_PIPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_PIPE_PREFIX = "\\\\.\\pipe" + chr(92)


def installation_pipe_name(installation_id: str) -> str:
    """Return the stable local pipe path for one installation identity."""

    if not isinstance(installation_id, str) or not _PIPE_NAME_RE.fullmatch(installation_id):
        raise ValueError("installation_id must be a short safe pipe-name component")
    return rf"\\.\pipe\haven-{installation_id}"


def installation_id_for_data_dir(data_dir: str | os.PathLike[str]) -> str:
    """Derive a stable non-secret pipe identifier without exposing the path."""

    canonical = os.path.normcase(os.path.abspath(os.fspath(data_dir)))
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()[:32]


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    _KERNEL32.CreateNamedPipeW.restype = wintypes.HANDLE
    _KERNEL32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    _KERNEL32.ConnectNamedPipe.restype = wintypes.BOOL
    _KERNEL32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    _KERNEL32.DisconnectNamedPipe.restype = wintypes.BOOL
    _KERNEL32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _KERNEL32.WriteFile.restype = wintypes.BOOL
    _KERNEL32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _KERNEL32.FlushFileBuffers.restype = wintypes.BOOL
    _KERNEL32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    _KERNEL32.CancelIoEx.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("HAVEN native IPC currently requires Windows named pipes")


def _last_error(message: str) -> OSError:
    return OSError(ctypes.get_last_error(), message)


def _handle_value(handle: object) -> int:
    return int(getattr(handle, "value", handle) or 0)


class NamedPipeServer:
    """Serve authenticated framed requests on one local Windows pipe."""

    def __init__(
        self,
        *,
        pipe_name: str,
        auth_token: str,
        handler: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    ) -> None:
        _require_windows()
        if not isinstance(pipe_name, str) or not pipe_name.startswith(_PIPE_PREFIX):
            raise ValueError("pipe_name must use the local named-pipe namespace")
        if not isinstance(auth_token, str) or not auth_token:
            raise ValueError("auth_token must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self.pipe_name = pipe_name
        self._auth_token = auth_token
        self._handler = handler
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handles: set[int] = set()
        self._handles_lock = threading.Lock()
        self._authenticated = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def authenticated_once(self) -> bool:
        """Whether at least one client completed the application handshake."""

        return self._authenticated.is_set()

    def start(self) -> "NamedPipeServer":
        if self.is_running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="haven-ipc-pipe", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._handles_lock:
            handles = tuple(self._handles)
        for handle in handles:
            try:
                _KERNEL32.CancelIoEx(handle, None)
            except OSError:
                pass
            try:
                _KERNEL32.DisconnectNamedPipe(handle)
            except OSError:
                pass
            try:
                _KERNEL32.CloseHandle(handle)
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def _new_handle(self) -> int:
        handle = _KERNEL32.CreateNamedPipeW(
            self.pipe_name,
            _PIPE_ACCESS_DUPLEX,
            _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
            1,
            MAX_FRAME_BYTES + 4,
            MAX_FRAME_BYTES + 4,
            0,
            None,
        )
        value = _handle_value(handle)
        if value == _INVALID_HANDLE_VALUE or value == 0:
            raise _last_error("could not create HAVEN named pipe")
        with self._handles_lock:
            self._handles.add(value)
        return value

    def _close_handle(self, handle: int) -> None:
        with self._handles_lock:
            self._handles.discard(handle)
        try:
            _KERNEL32.DisconnectNamedPipe(handle)
        finally:
            _KERNEL32.CloseHandle(handle)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                handle = self._new_handle()
            except OSError:
                if not self._stop.is_set():
                    self._stop.wait(0.25)
                continue
            try:
                connected = bool(_KERNEL32.ConnectNamedPipe(handle, None))
                if not connected:
                    error = ctypes.get_last_error()
                    if error != _ERROR_PIPE_CONNECTED:
                        if error in (_ERROR_OPERATION_ABORTED, _ERROR_NO_DATA) or self._stop.is_set():
                            continue
                        continue
                if not self._stop.is_set():
                    self._serve_client(handle)
            finally:
                self._close_handle(handle)

    def _serve_client(self, handle: int) -> None:
        authenticated = False
        while not self._stop.is_set():
            try:
                message = self._read_message(handle)
            except (EOFError, OSError, IpcProtocolError):
                return
            if message.get("kind") != "request":
                return
            request_id = message["request_id"]
            if not authenticated:
                params = message.get("params", {})
                if message.get("method") != "host.authenticate" or not isinstance(params.get("token"), str) or not hmac.compare_digest(
                    params["token"], self._auth_token
                ):
                    try:
                        self._write_message(
                            handle,
                            response_message(request_id, ok=False, error="native IPC authentication failed"),
                        )
                    except (EOFError, OSError):
                        # A client may disconnect immediately after sending a
                        # bad token.  That is a normal transport race, not an
                        # unhandled server-thread failure.
                        return
                    return
                authenticated = True
                self._authenticated.set()
                try:
                    self._write_message(
                        handle, response_message(request_id, ok=True, result={"authenticated": True})
                    )
                except (EOFError, OSError):
                    # The native client can close after receiving the
                    # authentication response.  Treat a broken pipe during
                    # that response as an ordinary end of the session.
                    return
                continue
            try:
                response = self._handler(message)
                if response is None:
                    response = response_message(request_id, ok=True, result=None)
                elif response.get("request_id") != request_id:
                    raise IpcProtocolError("IPC handler returned a mismatched request_id")
                self._write_message(handle, response)
            except Exception:
                try:
                    self._write_message(
                        handle,
                        response_message(request_id, ok=False, error="HAVEN could not complete the IPC request"),
                    )
                except (EOFError, OSError):
                    return

    @staticmethod
    def _read_exact(handle: int, length: int) -> bytes:
        buffer = (ctypes.c_char * length)()
        offset = 0
        while offset < length:
            count = wintypes.DWORD()
            ok = _KERNEL32.ReadFile(handle, ctypes.byref(buffer, offset), length - offset, ctypes.byref(count), None)
            if not ok:
                error = ctypes.get_last_error()
                if error in (_ERROR_BROKEN_PIPE, _ERROR_NO_DATA):
                    raise EOFError
                raise _last_error("could not read HAVEN named pipe")
            if count.value == 0:
                raise EOFError
            offset += count.value
        return bytes(buffer)

    def _read_message(self, handle: int) -> dict[str, Any]:
        header = self._read_exact(handle, _HEADER.size)
        (length,) = _HEADER.unpack(header)
        if length <= 0 or length > MAX_FRAME_BYTES:
            raise IpcProtocolError("invalid IPC frame length")
        return decode_frame(self._read_exact(handle, length))

    @staticmethod
    def _write_message(handle: int, message: Mapping[str, Any]) -> None:
        frame = encode_frame(message)
        buffer = ctypes.create_string_buffer(frame)
        offset = 0
        while offset < len(frame):
            count = wintypes.DWORD()
            ok = _KERNEL32.WriteFile(
                handle,
                ctypes.byref(buffer, offset),
                len(frame) - offset,
                ctypes.byref(count),
                None,
            )
            if not ok:
                raise _last_error("could not write HAVEN named pipe")
            if count.value == 0:
                raise EOFError
            offset += count.value
        if not _KERNEL32.FlushFileBuffers(handle):
            raise _last_error("could not flush HAVEN named pipe")


__all__ = ["NamedPipeServer", "installation_id_for_data_dir", "installation_pipe_name"]
