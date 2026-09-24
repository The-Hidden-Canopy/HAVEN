"""Transitional native-client host for the Python HAVEN Core.

The current composition still lives in ``HavenWebServer`` while application
services are being extracted.  This host deliberately does not route the
native client through HTTP: it attaches a named pipe directly to the same
server-owned services and can be removed once the composition root no longer
needs the compatibility web server.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from haven.ipc.events_pipe import (
    EventPublisher,
    NamedPipeEventServer,
    derive_events_pipe_name,
)
from haven.ipc.named_pipe import NamedPipeServer, installation_id_for_data_dir, installation_pipe_name


class NativeIpcHost:
    """Expose one authenticated Core instance to the native client.

    Serves the request/response RPC pipe plus its one-directional sibling,
    ``haven-events-<installation-id>``, which pushes domain-invalidation
    events after the same token handshake (spec sections 15-17).
    """

    def __init__(self, *, server, data_dir: str | Path, auth_token: str | None = None) -> None:
        self.server = server
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.installation_id = installation_id_for_data_dir(self.data_dir)
        self.pipe_name = installation_pipe_name(self.installation_id)
        self.events_pipe_name = derive_events_pipe_name(self.pipe_name)
        self.auth_token = auth_token or secrets.token_urlsafe(32)
        self._pipe: NamedPipeServer | None = None
        self._events_pipe: NamedPipeEventServer | None = None

    @property
    def is_running(self) -> bool:
        return self._pipe is not None and self._pipe.is_running

    @property
    def authenticated_once(self) -> bool:
        return self._pipe is not None and self._pipe.authenticated_once

    def start(self) -> "NativeIpcHost":
        if self._pipe is None:
            self._pipe = NamedPipeServer(
                pipe_name=self.pipe_name,
                auth_token=self.auth_token,
                handler=self.server.build_ipc_dispatcher(),
            ).start()
        if self._events_pipe is None:
            publisher = getattr(self.server, "events", None)
            if publisher is None:  # fail soft for non-web compositions
                publisher = EventPublisher()
            self._events_pipe = NamedPipeEventServer(
                pipe_name=self.events_pipe_name,
                auth_token=self.auth_token,
                publisher=publisher,
            ).start()
        return self

    def stop(self) -> None:
        pipe = self._pipe
        self._pipe = None
        if pipe is not None:
            pipe.stop()
        events_pipe = self._events_pipe
        self._events_pipe = None
        if events_pipe is not None:
            events_pipe.stop()


__all__ = ["NativeIpcHost"]
