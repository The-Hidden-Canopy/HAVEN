"""Explicit host lifecycle: no autostart, provisioning, or trust changes."""
from __future__ import annotations

import threading
from . import BridgeServer


class ManagedBridgeHost:
    def __init__(self, address, bridge, subjects, tls_context, *, services=None, max_connections=16, product_host=None, pending_checker=None, session_host=None, feature_host=None):
        self._args = (address, bridge, subjects, tls_context)
        self._options = {"services": services, "max_connections": max_connections, "product_host": product_host, "pending_checker": pending_checker, "session_host": session_host, "feature_host":feature_host}
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    def start(self):
        with self._lock:
            if self._server is not None:
                raise RuntimeError("bridge host already started")
            server = BridgeServer(*self._args, **self._options)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, name="haven-android-bridge", daemon=True)
            try:
                thread.start()
            except BaseException:
                server.server_close()
                raise
            self._server, self._thread = server, thread
            return server.server_address

    def stop(self):
        with self._lock:
            server, thread = self._server, self._thread
            if server is None:
                return
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            self._server = self._thread = None

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()
