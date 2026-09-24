"""Contract tests for the native events pipe (spec sections 15-17).

Covers the event vocabulary, publisher coalescing/heartbeat semantics, the
push-only pipe (authentication, event frames, lifecycle frames), and the
server-side emitters that publish domain invalidations.
"""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from haven.ipc import encode_frame, request_message
from haven.ipc.events_pipe import (
    EVENT_DOMAINS,
    EventPublisher,
    NamedPipeEventServer,
    derive_events_pipe_name,
)
from haven.ipc.named_pipe import installation_pipe_name

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe transport")

SPEC_VOCABULARY = {
    "core.connected",
    "core.degraded",
    "core.shutdown",
    "tasks.changed",
    "projects.changed",
    "relationships.changed",
    "memory.changed",
    "search.index.changed",
    "computer.files.changed",
    "computer.windows.changed",
    "computer.activity.changed",
    "email.changed",
    "calendar.changed",
    "browser.tabs.changed",
    "home.state.changed",
    "authority.pending.changed",
    "models.changed",
    "model.job.progress",
    "speech.state.changed",
}


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


def _read_message(kernel32, handle) -> dict:
    header = _read(kernel32, handle, 4)
    (length,) = int.from_bytes(header, "little"), 
    body = _read(kernel32, handle, length)
    return json.loads(body.decode("utf-8"))


def _connect(pipe_name: str):
    kernel32 = _kernel32()
    for _ in range(40):
        handle = kernel32.CreateFileW(pipe_name, 0xC0000000, 0, None, 3, 0, None)
        value = int(getattr(handle, "value", handle) or 0)
        if value not in (0, ctypes.c_void_p(-1).value):
            return kernel32, value
        time.sleep(0.025)
    raise OSError(ctypes.get_last_error(), "could not connect to the test named pipe")


def _auth(kernel32, handle, token: str) -> dict:
    _write(
        kernel32,
        handle,
        encode_frame(request_message("auth", "host.authenticate", {"token": token})),
    )
    return _read_message(kernel32, handle)


def test_events_pipe_name_is_derived_from_the_rpc_pipe_name():
    rpc = installation_pipe_name("install123")
    events = derive_events_pipe_name(rpc)
    assert events.endswith("haven-events-install123")
    assert "haven-events-" in events
    with pytest.raises(ValueError):
        derive_events_pipe_name("not-a-pipe")
    with pytest.raises(ValueError):
        derive_events_pipe_name("\\\\.\\pipe\\other-install123")


def test_event_vocabulary_matches_the_spec_taxonomy():
    assert set(EVENT_DOMAINS) == SPEC_VOCABULARY


def test_event_vocabulary_has_a_domain_for_every_event():
    assert all(domain.strip() for domain in EVENT_DOMAINS.values())


def test_publisher_rejects_unknown_event_names():
    publisher = EventPublisher()
    with pytest.raises(ValueError):
        publisher.publish("tasks.deleted")


def test_publisher_coalesces_bursts_to_the_newest_payload():
    publisher = EventPublisher(flush_interval=0.05)
    delivered: list[dict] = []
    publisher.subscribe(delivered.append)
    try:
        publisher.publish("tasks.changed", note="first")
        publisher.publish("tasks.changed", note="second")
        publisher.publish("projects.changed")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(delivered) < 2:
            time.sleep(0.02)
        assert len(delivered) == 2
        by_event = {frame["event"]: frame for frame in delivered}
        assert set(by_event) == {"tasks.changed", "projects.changed"}
        assert by_event["tasks.changed"]["payload"]["note"] == "second"
        assert by_event["tasks.changed"]["payload"]["domain"] == "tasks"
        assert by_event["projects.changed"]["payload"]["domain"] == "projects"
        first = by_event["tasks.changed"]["payload"]
        second = by_event["projects.changed"]["payload"]
        assert first["revision"] < second["revision"]
        assert "at" in first
    finally:
        publisher.stop()


def test_publisher_emits_heartbeat_frames():
    publisher = EventPublisher(flush_interval=0.05, heartbeat_interval=0.1)
    delivered: list[dict] = []
    publisher.subscribe(delivered.append)
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            frame["event"] == "heartbeat" for frame in delivered
        ):
            time.sleep(0.02)
        assert any(frame["event"] == "heartbeat" for frame in delivered)
    finally:
        publisher.stop()


def test_publisher_flushes_remaining_frames_on_stop():
    publisher = EventPublisher(flush_interval=60.0)
    delivered: list[dict] = []
    publisher.subscribe(delivered.append)
    publisher.publish("memory.changed")
    publisher.stop()
    assert [frame["event"] for frame in delivered] == ["memory.changed"]


def test_event_pipe_requires_the_shared_auth_token():
    publisher = EventPublisher(flush_interval=0.05)
    server = NamedPipeEventServer(
        pipe_name=installation_pipe_name("test-events-auth"),
        auth_token="events-secret",
        publisher=publisher,
    ).start()
    kernel32 = None
    handle = None
    try:
        kernel32, handle = _connect(server.pipe_name)
        response = _auth(kernel32, handle, "wrong-secret")
        assert response["ok"] is False
        assert "authentication failed" in response["error"]
    finally:
        if kernel32 is not None and handle is not None:
            kernel32.CloseHandle(handle)
        server.stop()
        publisher.stop()


def test_event_pipe_pushes_events_after_authentication():
    publisher = EventPublisher(flush_interval=0.05, heartbeat_interval=60.0)
    server = NamedPipeEventServer(
        pipe_name=installation_pipe_name("test-events-push"),
        auth_token="events-secret",
        publisher=publisher,
    ).start()
    kernel32 = None
    handle = None
    try:
        kernel32, handle = _connect(server.pipe_name)
        auth = _auth(kernel32, handle, "events-secret")
        assert auth["ok"] is True

        # The lifecycle frame arrives first: the pipe is live.
        first = _read_message(kernel32, handle)
        assert first["kind"] == "event"
        assert first["event"] == "core.connected"

        publisher.publish("tasks.changed", origin="test")
        frame = _read_message(kernel32, handle)
        assert frame["kind"] == "event"
        assert frame["event"] == "tasks.changed"
        assert frame["payload"]["domain"] == "tasks"
        assert frame["payload"]["origin"] == "test"
        assert frame["payload"]["revision"] > first["payload"]["revision"]
    finally:
        if kernel32 is not None and handle is not None:
            kernel32.CloseHandle(handle)
        server.stop()
        publisher.stop()


def test_event_pipe_is_push_only_after_authentication():
    publisher = EventPublisher(flush_interval=0.05, heartbeat_interval=60.0)
    server = NamedPipeEventServer(
        pipe_name=installation_pipe_name("test-events-push-only"),
        auth_token="events-secret",
        publisher=publisher,
    ).start()
    kernel32 = None
    handle = None
    try:
        kernel32, handle = _connect(server.pipe_name)
        assert _auth(kernel32, handle, "events-secret")["ok"] is True
        _read_message(kernel32, handle)  # core.connected

        _write(
            kernel32,
            handle,
            encode_frame(request_message("sneaky", "tasks.list", {})),
        )
        response = _read_message(kernel32, handle)
        assert response["kind"] == "response"
        assert response["ok"] is False
        assert "push-only" in response["error"]
    finally:
        if kernel32 is not None and handle is not None:
            kernel32.CloseHandle(handle)
        server.stop()
        publisher.stop()


def test_event_pipe_publishes_shutdown_on_stop():
    publisher = EventPublisher(flush_interval=0.05, heartbeat_interval=60.0)
    server = NamedPipeEventServer(
        pipe_name=installation_pipe_name("test-events-shutdown"),
        auth_token="events-secret",
        publisher=publisher,
    ).start()
    kernel32 = None
    handle = None
    frames: list[dict] = []
    try:
        kernel32, handle = _connect(server.pipe_name)
        assert _auth(kernel32, handle, "events-secret")["ok"] is True

        # Read concurrently, the way the real client does: the shutdown frame
        # is pushed between publish and handle teardown, and a client that
        # waits to read until after close would find the buffer discarded.
        def reader():
            while True:
                try:
                    frames.append(_read_message(kernel32, handle))
                except Exception:
                    return

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            frame["event"] == "core.connected" for frame in frames
        ):
            time.sleep(0.02)
        assert any(frame["event"] == "core.connected" for frame in frames)

        server.stop()
        thread.join(timeout=5)
        assert any(frame["event"] == "core.shutdown" for frame in frames)
    finally:
        if kernel32 is not None and handle is not None:
            kernel32.CloseHandle(handle)
        server.stop()
        publisher.stop()


def test_event_pipe_reaccepts_after_client_disconnects():
    publisher = EventPublisher(flush_interval=0.05, heartbeat_interval=60.0)
    server = NamedPipeEventServer(
        pipe_name=installation_pipe_name("test-events-reconnect"),
        auth_token="events-secret",
        publisher=publisher,
    ).start()
    kernel32 = _kernel32()
    try:
        first = _connect(server.pipe_name)
        assert _auth(first[0], first[1], "events-secret")["ok"] is True
        _read_message(first[0], first[1])  # core.connected
        first[0].CloseHandle(first[1])

        # The server must accept (and re-authenticate) a second client.
        second = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                second = _connect(server.pipe_name)
                break
            except OSError:
                time.sleep(0.05)
        assert second is not None
        assert _auth(second[0], second[1], "events-secret")["ok"] is True
        frame = _read_message(second[0], second[1])
        assert frame["event"] == "core.connected"
        second[0].CloseHandle(second[1])
    finally:
        server.stop()
        publisher.stop()


@pytest.fixture()
def server():
    from haven.web.server import make_server

    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data")
        try:
            yield instance
        finally:
            instance.server_close()


def _dispatch(instance, method: str, params: dict) -> dict:
    from haven.ipc import request_message

    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def test_task_mutation_emits_tasks_and_relationships_events(server):
    delivered: list[dict] = []
    unsubscribe = server.events.subscribe(delivered.append)
    try:
        created = _dispatch(server, "tasks.create", {"title": "Wire the emitters"})
        assert created["ok"] is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not {
            frame["event"] for frame in delivered
        } >= {"tasks.changed", "relationships.changed", "search.index.changed"}:
            time.sleep(0.02)
        events = {frame["event"] for frame in delivered}
        assert {"tasks.changed", "relationships.changed", "search.index.changed"} <= events
        assert "projects.changed" not in events
    finally:
        unsubscribe()


def test_room_mutation_emits_home_state_changed(server):
    delivered: list[dict] = []
    unsubscribe = server.events.subscribe(delivered.append)
    try:
        created = _dispatch(server, "rooms.add", {"name": "Studio"})
        assert created["ok"] is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            frame["event"] == "home.state.changed" for frame in delivered
        ):
            time.sleep(0.02)
        assert any(frame["event"] == "home.state.changed" for frame in delivered)
    finally:
        unsubscribe()


def test_failed_mutation_emits_nothing(server, monkeypatch):
    from types import SimpleNamespace

    principal = SimpleNamespace(actor_id="actor-owner-1")
    monkeypatch.setattr(server.director, "owner", principal, raising=False)
    monkeypatch.setattr(server.director, "has_declared_owner", True, raising=False)

    delivered: list[dict] = []
    unsubscribe = server.events.subscribe(delivered.append)
    try:
        corrected = _dispatch(
            server,
            "knowledge.claim.correct",
            {"claim_id": "missing-claim", "proposition": "ignored"},
        )
        # Unknown claim fails at the adapter; no invalidation may fire.
        assert corrected["ok"] is False
        assert delivered == []
    finally:
        unsubscribe()


def test_models_mutation_emits_models_changed(server):
    delivered: list[dict] = []
    unsubscribe = server.events.subscribe(delivered.append)
    try:
        # scan against an empty root always succeeds and lands the registry
        result = _dispatch(server, "models.scan", {})
        assert result["ok"] is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            frame["event"] == "models.changed" for frame in delivered
        ):
            time.sleep(0.02)
        assert any(frame["event"] == "models.changed" for frame in delivered)
    finally:
        unsubscribe()
