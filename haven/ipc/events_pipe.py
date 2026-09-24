"""Push-only event pipe for the native HAVEN client (spec sections 15–17).

The RPC pipe stays the only request/response channel.  This module adds its
one-directional sibling, ``haven-events-<installation-id>``: after the same
``host.authenticate`` handshake, the Core pushes event frames and never reads
application requests again.  Events carry invalidations (``{event, domain,
revision, at}`` plus optional summary fields), never row payloads — the client
re-loads the domain through the RPC pipe it already owns.

Delivery is coalesced: bursts of the same event name collapse to the newest
payload, flushed on a short timer.  A heartbeat frame every 15 seconds lets
the client detect a silently stale pipe even when no domain changes.
"""

from __future__ import annotations

import ctypes
import hmac
import threading
import time
from collections.abc import Callable, Mapping
from ctypes import wintypes
from datetime import datetime, timezone
from typing import Any

from .named_pipe import _KERNEL32, NamedPipeServer
from .protocol import IpcProtocolError, encode_frame, event_message, response_message

# The event vocabulary (spec section 15).  Values are the domains used for
# client-side invalidation; "core" frames describe the connection itself.
EVENT_DOMAINS: dict[str, str] = {
    "core.connected": "core",
    "core.degraded": "core",
    "core.shutdown": "core",
    "tasks.changed": "tasks",
    "projects.changed": "projects",
    "relationships.changed": "relationships",
    "memory.changed": "memory",
    "search.index.changed": "search",
    "computer.files.changed": "computer",
    "computer.windows.changed": "computer",
    "computer.activity.changed": "computer",
    "email.changed": "comms",
    "calendar.changed": "comms",
    "browser.tabs.changed": "browser",
    "home.state.changed": "home",
    "authority.pending.changed": "authority",
    "models.changed": "models",
    "model.job.progress": "models",
    "speech.state.changed": "speech",
}

_EVENTS_PREFIX = "haven-events-"
_RPC_PREFIX = "haven-"
_PIPE_PREFIX = "\\\\.\\pipe\\"


def _push_message(handle: int, message: Mapping[str, Any]) -> None:
    """Write one frame without the base class's server-side flush.

    ``FlushFileBuffers`` on a pipe server blocks until the client has read
    every buffered byte; on a push-only channel that lets a momentarily idle
    client stall the publisher.  Byte-mode pipes deliver written bytes to the
    reader without an explicit flush.
    """
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
            raise OSError(ctypes.get_last_error(), "could not write HAVEN events pipe")
        if count.value == 0:
            raise EOFError
        offset += count.value


def derive_events_pipe_name(rpc_pipe_name: str) -> str:
    """Derive the push-only events pipe name from the RPC pipe name.

    ``\\\\.\\pipe\\haven-<installation-id>`` becomes
    ``\\\\.\\pipe\\haven-events-<installation-id>``.
    """

    if not isinstance(rpc_pipe_name, str) or not rpc_pipe_name.startswith(_PIPE_PREFIX):
        raise ValueError("pipe_name must use the local named-pipe namespace")
    base = rpc_pipe_name[len(_PIPE_PREFIX):]
    if not base.startswith(_RPC_PREFIX):
        raise ValueError("rpc pipe name must use the haven- prefix")
    return _PIPE_PREFIX + _EVENTS_PREFIX + base[len(_RPC_PREFIX):]


class EventPublisher:
    """In-process event bus with keep-last-per-name coalescing.

    Mutations call :meth:`publish` from any thread.  Subscribers receive
    validated event envelopes on a single flush thread, at most once per
    flush interval per event name — only the newest payload survives a burst.
    Heartbeat frames bypass coalescing so the client can always detect a
    stalled pipe.
    """

    def __init__(
        self,
        *,
        flush_interval: float = 0.2,
        heartbeat_interval: float = 15.0,
    ) -> None:
        if flush_interval <= 0 or heartbeat_interval <= 0:
            raise ValueError("intervals must be positive")
        self._flush_interval = float(flush_interval)
        self._heartbeat_interval = float(heartbeat_interval)
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._revision = 0
        self._flush_timer: threading.Timer | None = None
        self._heartbeat_timer: threading.Timer | None = None
        self._stopped = False

    @property
    def flush_interval(self) -> float:
        """Seconds between coalescing flushes (the stop() grace window)."""
        return self._flush_interval

    @property
    def revision(self) -> int:
        """Monotonic counter; every published event advances it."""

        with self._lock:
            return self._revision

    def publish(self, event: str, *, coalesce: bool = True, **data: Any) -> dict[str, Any]:
        """Queue one event.  Returns the envelope that will be delivered.

        ``coalesce=False`` is for lifecycle frames (core.connected,
        core.shutdown) that must not be dropped or merged.
        """

        if event not in EVENT_DOMAINS:
            raise ValueError(f"unknown HAVEN event name: {event!r}")
        with self._lock:
            if self._stopped:
                raise RuntimeError("EventPublisher is stopped")
            self._revision += 1
            payload: dict[str, Any] = {
                "domain": EVENT_DOMAINS[event],
                "revision": self._revision,
                "at": datetime.now(timezone.utc).isoformat(),
                **data,
            }
            envelope = event_message(event, payload)
            key = event if coalesce else f"{event}#{self._revision}"
            self._pending[key] = envelope
            self._schedule_flush_locked()
            return envelope

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Deliver future events to ``callback``; returns an unsubscribe."""

        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._stopped:
                raise RuntimeError("EventPublisher is stopped")
            self._subscribers.append(callback)
            if self._heartbeat_timer is None:
                self._heartbeat_timer = threading.Timer(
                    self._heartbeat_interval, self._heartbeat
                )
                self._heartbeat_timer.daemon = True
                self._heartbeat_timer.start()

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            pending, self._pending = self._pending, {}
            subscribers, self._subscribers = self._subscribers, []
            timers = (self._flush_timer, self._heartbeat_timer)
            self._flush_timer = None
            self._heartbeat_timer = None
        for timer in timers:
            if timer is not None:
                timer.cancel()
        for envelope in pending.values():
            self._deliver(envelope, subscribers)

    def _schedule_flush_locked(self) -> None:
        if self._flush_timer is not None or not self._subscribers:
            # Without subscribers there is nothing to flush against; pending
            # frames for a future subscriber would be stale anyway, so drop
            # them at the next publish boundary.
            if not self._subscribers:
                self._pending.clear()
            return
        self._flush_timer = threading.Timer(self._flush_interval, self._flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush(self) -> None:
        with self._lock:
            self._flush_timer = None
            if self._stopped:
                return
            pending, self._pending = self._pending, {}
            subscribers = tuple(self._subscribers)
        for envelope in pending.values():
            self._deliver(envelope, subscribers)

    def _heartbeat(self) -> None:
        with self._lock:
            self._heartbeat_timer = None
            if self._stopped:
                return
            self._revision += 1
            envelope = event_message(
                "heartbeat",
                {"revision": self._revision, "at": datetime.now(timezone.utc).isoformat()},
            )
            subscribers = tuple(self._subscribers)
            self._heartbeat_timer = threading.Timer(
                self._heartbeat_interval, self._heartbeat
            )
            self._heartbeat_timer.daemon = True
            self._heartbeat_timer.start()
        self._deliver(envelope, subscribers)

    @staticmethod
    def _deliver(envelope: Mapping[str, Any], subscribers: tuple[Callable[[dict[str, Any]], None], ...]) -> None:
        for callback in subscribers:
            try:
                callback(envelope)
            except Exception:
                # A slow or broken client must never stall the core's
                # mutation path; the pipe layer reports the real failure.
                pass


class NamedPipeEventServer(NamedPipeServer):
    """Push-only sibling pipe: authenticate, then receive event frames.

    The pipe accepts one client at a time.  After ``host.authenticate``
    succeeds, the server publishes ``core.connected`` and forwards every
    published event frame until the client disconnects, at which point the
    accept loop waits for the next client (which re-syncs via its RPC pipe).
    """

    def __init__(self, *, pipe_name: str, auth_token: str, publisher: EventPublisher) -> None:
        super().__init__(pipe_name=pipe_name, auth_token=auth_token, handler=self._reject_request)
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must be an EventPublisher")
        self._publisher = publisher
        self._write_lock = threading.Lock()
        self._current_handle: int | None = None
        self._unsubscribe: Callable[[], None] | None = None

    def start(self) -> "NamedPipeEventServer":
        if self._unsubscribe is None:
            self._unsubscribe = self._publisher.subscribe(self._on_event)
        super().start()
        return self

    def stop(self) -> None:
        try:
            # Delivered while the client (if any) is still connected, before
            # the transport is torn down.
            self._publisher.publish("core.shutdown", coalesce=False)
        except (RuntimeError, ValueError):
            pass
        # The frame leaves on the next flush; DisconnectNamedPipe discards
        # unread buffered output, so give the client one flush plus a small
        # grace before the handle is torn down.
        time.sleep(self._publisher.flush_interval + 0.1)
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()
        super().stop()

    def _reject_request(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        # Requests after authentication are a protocol violation; the adapter
        # must use the RPC pipe.  Returning the envelope lets the base class
        # write the correlated error response.
        return response_message(
            str(message.get("request_id", "")),
            ok=False,
            error="the events pipe is push-only; use the RPC pipe",
        )

    def _on_event(self, envelope: Mapping[str, Any]) -> None:
        handle = self._current_handle
        if handle is None:
            return
        with self._write_lock:
            try:
                _push_message(handle, envelope)
            except (EOFError, OSError, IpcProtocolError):
                # The client is gone; the blocked reader will notice and the
                # accept loop will serve the next connection.
                pass

    def _serve_client(self, handle: int) -> None:
        authenticated = False
        self._current_handle = handle
        try:
            while not self._stop.is_set():
                if authenticated:
                    # Push-only phase.  A pending blocking ReadFile would
                    # monopolize this synchronous handle and deadlock every
                    # writer thread, so poll instead: PeekNamedPipe reports
                    # client disconnects and any protocol-violating request
                    # without holding the handle hostage.
                    try:
                        waiting = self._peek(handle)
                    except OSError:
                        return
                    if waiting == 0:
                        self._stop.wait(0.2)
                        continue
                try:
                    message = self._read_message(handle)
                except (EOFError, OSError, IpcProtocolError):
                    return
                if message.get("kind") != "request":
                    return
                request_id = message["request_id"]
                if not authenticated:
                    params = message.get("params", {})
                    if message.get("method") != "host.authenticate" or not isinstance(
                        params.get("token"), str
                    ) or not hmac.compare_digest(params["token"], self._auth_token):
                        try:
                            with self._write_lock:
                                # The client is blocked waiting for this
                                # response; the rendezvous flush guarantees
                                # delivery before the handle is released.
                                self._write_message(
                                    handle,
                                    response_message(
                                        request_id,
                                        ok=False,
                                        error="native IPC authentication failed",
                                    ),
                                )
                        except (EOFError, OSError):
                            return
                        return
                    authenticated = True
                    self._authenticated.set()
                    try:
                        with self._write_lock:
                            self._write_message(
                                handle,
                                response_message(request_id, ok=True, result={"authenticated": True}),
                            )
                        self._publisher.publish("core.connected", coalesce=False)
                    except (EOFError, OSError):
                        return
                    continue
                # Authenticated: the events pipe never serves requests.
                try:
                    with self._write_lock:
                        self._write_message(
                            handle,
                            response_message(
                                request_id,
                                ok=False,
                                error="the events pipe is push-only; use the RPC pipe",
                            ),
                        )
                except (EOFError, OSError):
                    return
        finally:
            self._current_handle = None

    @staticmethod
    def _peek(handle: int) -> int:
        """Bytes the client has sent, or raise OSError if it disconnected."""
        available = wintypes.DWORD()
        if not _KERNEL32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            raise OSError(ctypes.get_last_error(), "HAVEN events client disconnected")
        return int(available.value)


__all__ = [
    "EVENT_DOMAINS",
    "EventPublisher",
    "NamedPipeEventServer",
    "derive_events_pipe_name",
]
