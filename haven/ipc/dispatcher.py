"""Application-facing method dispatch for native HAVEN clients.

The dispatcher is deliberately callback-based.  It knows the IPC envelope,
request correlation, and fail-closed method lookup, but it does not know how a
room, search result, or file action is stored.  The composition root supplies
callbacks that already own those behaviors, so a native client cannot create
a second mutation path around HAVEN authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .protocol import IpcProtocolError, response_message

Handler = Callable[[Mapping[str, Any]], Any]


class IpcDispatcher:
    """Turn one authenticated request into one correlated response."""

    def __init__(self, handlers: Mapping[str, Handler]) -> None:
        self._handlers = dict(handlers)

    def __call__(self, message: Mapping[str, Any]) -> dict[str, Any]:
        if message.get("kind") != "request":
            raise IpcProtocolError("only request messages may reach the dispatcher")
        request_id = message.get("request_id")
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(request_id, str) or not request_id:
            raise IpcProtocolError("request_id is required")
        if not isinstance(method, str) or not method:
            raise IpcProtocolError("method is required")
        if not isinstance(params, Mapping):
            raise IpcProtocolError("request params must be an object")
        handler = self._handlers.get(method)
        if handler is None:
            return response_message(request_id, ok=False, error=f"unknown IPC method: {method}")
        try:
            result = handler(dict(params))
        except (ValueError, KeyError, TypeError) as exc:
            # Input failures are safe to explain to the client.  Unexpected
            # exceptions are handled by the transport with a generic error so
            # implementation details never cross the native boundary.
            return response_message(request_id, ok=False, error=str(exc) or "invalid IPC request")
        return response_message(request_id, ok=True, result=result)


__all__ = ["Handler", "IpcDispatcher"]
