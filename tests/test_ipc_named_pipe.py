"""Windows smoke coverage for the real named-pipe transport."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

import pytest

from haven.ipc import encode_frame, request_message, response_message
from haven.ipc.named_pipe import NamedPipeServer, installation_pipe_name

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe transport")


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _write(kernel32, handle, body: bytes) -> None:
    buffer = ctypes.create_string_buffer(body)
    written = wintypes.DWORD()
    assert kernel32.WriteFile(handle, buffer, len(body), ctypes.byref(written), None)
    assert written.value == len(body)


def _read(kernel32, handle, length: int) -> bytes:
    buffer = (ctypes.c_char * length)()
    read = wintypes.DWORD()
    assert kernel32.ReadFile(handle, buffer, length, ctypes.byref(read), None)
    assert read.value == length
    return bytes(buffer)


def _connect(pipe_name: str):
    kernel32 = _kernel32()
    for _ in range(40):
        handle = kernel32.CreateFileW(pipe_name, 0xC0000000, 0, None, 3, 0, None)
        value = int(getattr(handle, "value", handle) or 0)
        if value not in (0, ctypes.c_void_p(-1).value):
            return kernel32, value
        time.sleep(0.025)
    raise OSError(ctypes.get_last_error(), "could not connect to the test named pipe")


def test_named_pipe_requires_authentication_and_dispatches_a_framed_request():
    server = NamedPipeServer(
        pipe_name=installation_pipe_name("test-ipc-contract"),
        auth_token="test-secret",
        handler=lambda message: response_message(
            message["request_id"], ok=True, result={"echo": message["params"]}
        ),
    ).start()
    kernel32 = None
    handle = None
    try:
        kernel32, handle = _connect(server.pipe_name)
        _write(
            kernel32,
            handle,
            encode_frame(request_message("auth", "host.authenticate", {"token": "test-secret"})),
        )
        auth_body = _read(kernel32, handle, 4)
        auth_length = int.from_bytes(auth_body, "little")
        auth_response = _read(kernel32, handle, auth_length)
        assert b'"authenticated":true' in auth_response

        _write(
            kernel32,
            handle,
            encode_frame(request_message("read", "safe.read", {"value": "hello"})),
        )
        response_header = _read(kernel32, handle, 4)
        response_length = int.from_bytes(response_header, "little")
        response = _read(kernel32, handle, response_length)
        assert b'"echo":{"value":"hello"}' in response
    finally:
        if kernel32 is not None and handle is not None:
            kernel32.CloseHandle(handle)
        server.stop()


def test_named_pipe_rejects_a_wrong_token_before_dispatching_application_methods():
    called = []
    server = NamedPipeServer(
        pipe_name=installation_pipe_name("test-ipc-auth-rejection"),
        auth_token="correct-secret",
        handler=lambda message: called.append(message) or response_message(
            message["request_id"], ok=True, result={}
        ),
    ).start()
    kernel32 = None
    handle = None
    try:
        kernel32, handle = _connect(server.pipe_name)
        _write(
            kernel32,
            handle,
            encode_frame(request_message("bad-auth", "host.authenticate", {"token": "wrong-secret"})),
        )
        response_header = _read(kernel32, handle, 4)
        response_length = int.from_bytes(response_header, "little")
        response = _read(kernel32, handle, response_length)
        assert b'"ok":false' in response
        assert b"authentication failed" in response
        assert called == []
    finally:
        if kernel32 is not None and handle is not None:
            kernel32.CloseHandle(handle)
        server.stop()


def test_named_pipe_auth_response_disconnect_does_not_escape_server_loop(monkeypatch):
    server = NamedPipeServer(
        pipe_name=installation_pipe_name("test-ipc-auth-disconnect"),
        auth_token="test-secret",
        handler=lambda message: response_message(message["request_id"], ok=True, result={}),
    )
    messages = iter((request_message("auth", "host.authenticate", {"token": "test-secret"}),))
    monkeypatch.setattr(server, "_read_message", lambda _handle: next(messages))

    def disconnected(_handle, _message):
        raise OSError("client disconnected while authentication response was flushing")

    monkeypatch.setattr(NamedPipeServer, "_write_message", staticmethod(disconnected))
    server._serve_client(1)
