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

from ..core import correlation
from .protocol import IpcProtocolError, response_message
from ..faults import public_failure, PUBLIC_ACTIONS

Handler = Callable[[Mapping[str, Any]], Any]


class IpcDispatcher:
    """Turn one authenticated request into one correlated response."""

    def __init__(self, handlers: Mapping[str, Handler], *, fault_journal=None) -> None:
        self._handlers = dict(handlers)
        self._fault_journal = fault_journal

    def _journal(self):
        return self._fault_journal() if callable(self._fault_journal) else self._fault_journal

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
        operation = method
        if method in {"features.action", "marketplace.product"} and isinstance(params.get("action"), str) and params["action"] in PUBLIC_ACTIONS:
            operation = method + "." + params["action"]
        handler = self._handlers.get(method)
        if handler is None:
            result = public_failure({"ok": False, "error": "unknown IPC method"}, "ipc.request", journal=self._journal(), correlation_id=request_id)
            response = response_message(request_id, ok=False, error=result["error"])
            response["fault"] = result["fault"]
            return response
        try:
            # The wire's own request_id doubles as the correlation id: it is
            # already unique per call and already crosses the native
            # boundary, so nothing new has to be invented or threaded in from
            # outside. Anything downstream (a ledger entry, a log line) that
            # wants to know "which request caused this" reads
            # `haven.core.correlation.current()` rather than taking a new
            # parameter.
            with correlation.bind(request_id):
                result = handler(dict(params))
        except Exception as exc:
            result = public_failure({"ok": False, "error": str(exc)}, operation,
                                    journal=self._journal(), exception=exc, correlation_id=request_id)
            response = response_message(request_id, ok=False, error=result["error"])
            response["fault"] = result["fault"]
            return response
        result = public_failure(result, operation, journal=self._journal(), correlation_id=request_id)
        return response_message(request_id, ok=True, result=result)



__all__ = ["Handler", "IpcDispatcher"]
