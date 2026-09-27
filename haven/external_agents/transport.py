"""Transport bridge: the Python half of every external tool transport (WP2/WP3).

A transport adapter (the native MCP host in Haven.Desktop, any future
stdio/relay host) validates its own protocol envelope and hands this bridge
the few facts that matter: the presented credential, the tool name, the
tool arguments, and the protocol's request/session identifiers. Everything
else -- resolving the credential to a connection, building the
`ExternalRequest`, admission, scope checks, and dispatch into the governed
paths -- happens here, exactly once, so no transport ever re-implements
admission or grows its own policy (ADR-001/ADR-002: an external request is
never permission to mutate; the authority engine still decides).

The bridge never sees a raw secret at rest: the presented bearer credential
is hashed (SHA-256) and matched against the stored `credential_hash`; the
raw value is used for the lookup and discarded.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .domain import ExternalRequest, ExternalScope
from .errors import CONNECTION_UNKNOWN, INVALID_ARGUMENTS, ExternalDenied
from .gateway import ActionExecutor, ExternalAgentGateway
from .reads import READ_TOOL_SCOPES, ExternalReadTools
from .store import ExternalAgentStore

# The full external tool vocabulary: the read tools from `reads.py` plus the
# governed action path (`haven.action.*` → the gateway's executor methods).
# The transport declares exactly this scope in the `ExternalRequest` it
# (via this bridge) builds; a mismatch fails closed with INVALID_ARGUMENTS.
ACTION_TOOL_SCOPES: Mapping[str, ExternalScope] = {
    "haven.action.request": ExternalScope.ACTIONS_REQUEST,
    "haven.action.confirm": ExternalScope.ACTIONS_REQUEST,
    "haven.action.deny": ExternalScope.ACTIONS_REQUEST,
}

TOOL_SCOPES: Mapping[str, ExternalScope] = {**READ_TOOL_SCOPES, **ACTION_TOOL_SCOPES}


def hash_credential(credential: str) -> str:
    """SHA-256 of a presented bearer credential -- the only form the store holds."""

    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


class TransportBridge:
    """Resolves, admits, and dispatches one external tool call."""

    def __init__(
        self,
        *,
        store: ExternalAgentStore,
        gateway: ExternalAgentGateway,
        reads: ExternalReadTools,
        executor: ActionExecutor,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._reads = reads
        self._executor = executor

    def call_tool(
        self,
        *,
        credential: str,
        tool: str,
        external_request_id: str,
        arguments: Mapping[str, Any] | None = None,
        subject: str | None = None,
        subject_label: str | None = None,
        protocol_session_id: str | None = None,
        client_metadata: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one tool call end to end; every denial is an `ExternalDenied`.

        Returns the tool's result envelope on success (read payloads or the
        executor's ok-envelope); raises `ExternalDenied` before any dispatch
        for an unknown credential/tool, a mis-declared scope, malformed
        arguments, or any admission failure -- transports serialize the
        typed code verbatim.
        """

        if not isinstance(credential, str) or not credential.strip():
            raise ExternalDenied(CONNECTION_UNKNOWN, "no credential presented")
        connection = self._store.connection_for_credential(hash_credential(credential.strip()))
        if connection is None:
            # Same denial as an unknown connection id: a caller cannot
            # distinguish "no such credential" from "no such connection".
            raise ExternalDenied(CONNECTION_UNKNOWN, "unknown credential")

        declared = TOOL_SCOPES.get(tool) if isinstance(tool, str) else None
        if declared is None:
            raise ExternalDenied(INVALID_ARGUMENTS, f"unknown tool: {tool!r}")
        if not isinstance(external_request_id, str) or not external_request_id.strip():
            raise ExternalDenied(INVALID_ARGUMENTS, "a non-empty 'external_request_id' is required")

        request = ExternalRequest(
            connection_id=connection.connection_id,
            tool=tool,
            required_scope=declared,
            external_request_id=external_request_id,
            subject=subject,
            subject_label=subject_label,
            protocol_session_id=protocol_session_id,
            client_metadata=client_metadata or {},
        )
        admitted = self._gateway.admit(request)

        if tool in READ_TOOL_SCOPES:
            if tool == "haven.world.get":
                return self._reads.world_get(admitted)
            return self._reads.rooms_list(admitted)

        args = arguments if isinstance(arguments, Mapping) else {}
        if tool == "haven.action.request":
            return self._gateway.request_action(
                admitted,
                self._executor,
                device_id=_require_argument(args, "device_id"),
                service=_require_argument(args, "service"),
                parameters=args.get("parameters") if isinstance(args.get("parameters"), Mapping) else None,
            )
        if tool == "haven.action.confirm":
            return self._gateway.confirm_pending(admitted, self._executor, _require_argument(args, "request_id"))
        return self._gateway.deny_pending(admitted, self._executor, _require_argument(args, "request_id"))


def _require_argument(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ExternalDenied(INVALID_ARGUMENTS, f"a non-empty '{name}' argument is required")
    return value.strip()


__all__ = ["ACTION_TOOL_SCOPES", "TOOL_SCOPES", "TransportBridge", "hash_credential"]
