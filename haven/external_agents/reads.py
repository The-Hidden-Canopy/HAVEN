"""Read-only external tool surface (Build/Ship/Shape WP2/WP3 read path).

The action path (`ExternalAgentGateway.request_action`/`confirm_pending`/
`deny_pending`) leaves HAVEN through the authority engine; the read path
never does. These wrappers answer the tools an MCP transport exposes for
looking at the world (`haven.world.get`, `haven.rooms.list`) from the same
data the native/web surfaces already render, reached only through
`gateway.admit()` plus a per-tool scope re-check -- the same defense in
depth the action path applies, so an `AdmittedRequest` minted for one scope
can never be reused to read under another (spec 8.2).

Protocol-neutral (ADR-001): this module knows nothing about MCP or HTTP and
imports nothing from `haven.web`; the host application's read side arrives
as the `WorldReader` Protocol, which `HavenApplication.state` already
satisfies. A transport adapter stays thin: put the tool name and the scope
from `READ_TOOL_SCOPES` into an `ExternalRequest`, hand it to `call()`, and
shape the returned payload into its own result envelope.

Reads are intentionally unaudited here, matching the gateway: admission
security events (cross-household probes) are recorded by `admit()` itself;
an admitted read changes nothing and leaves no durable trace.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .domain import AdmittedRequest, ExternalRequest, ExternalScope
from .errors import INVALID_ARGUMENTS, ExternalDenied
from .gateway import ExternalAgentGateway
from .projections import WorldReadProjection

# The read-only tool vocabulary and the scope each tool requires. The
# transport adapter declares exactly this scope in the `ExternalRequest` it
# builds; `call()` refuses a request whose declared scope does not match the
# tool's own, so a mis-built request fails closed instead of being admitted
# against the wrong grant.
READ_TOOL_SCOPES: Mapping[str, ExternalScope] = {
    "haven.world.get": ExternalScope.WORLD_READ,
    "haven.rooms.list": ExternalScope.WORLD_READ,
}


class WorldReader(Protocol):
    """What the read tools need from the host application.

    `HavenApplication.state` satisfies this shape. Depending on the
    Protocol, not the concrete class, keeps `external_agents` free of any
    `haven.web` import (ADR-001) -- a future non-HAVEN host of this package
    supplies its own.
    """

    def state(self) -> Mapping[str, Any]: ...


class ExternalReadTools:
    """Admitted, scope-checked read-only tool handlers."""

    def __init__(self, *, gateway: ExternalAgentGateway, reader: WorldReader) -> None:
        self._gateway = gateway
        self._reader = reader

    def call(self, request: ExternalRequest, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Admit and dispatch one read-only tool call; unknown tools fail closed."""

        declared = READ_TOOL_SCOPES.get(request.tool)
        if declared is None:
            raise ExternalDenied(INVALID_ARGUMENTS, f"unknown read tool: {request.tool}")
        if request.required_scope is not declared:
            raise ExternalDenied(
                INVALID_ARGUMENTS,
                f"tool {request.tool} requires scope {declared.value}, not {request.required_scope.value}",
            )
        admitted = self._gateway.admit(request, correlation_id=correlation_id)
        if request.tool == "haven.world.get":
            return self.world_get(admitted)
        return self.rooms_list(admitted)

    def world_get(self, admitted: AdmittedRequest) -> dict[str, Any]:
        """Return only the explicit world projection for ``world.read``."""

        # Same-package seam: the gateway owns the scope-denial message.
        ExternalAgentGateway._require_scope(admitted, ExternalScope.WORLD_READ)  # noqa: SLF001
        projection = WorldReadProjection.from_state(self._reader.state()).to_dict()
        return {"ok": True, "state": projection}

    def rooms_list(self, admitted: AdmittedRequest) -> dict[str, Any]:
        """Return rooms from the same explicit world projection."""

        ExternalAgentGateway._require_scope(admitted, ExternalScope.WORLD_READ)  # noqa: SLF001
        projection = WorldReadProjection.from_state(self._reader.state()).to_dict()
        return {"ok": True, "rooms": projection["rooms"]}


__all__ = ["READ_TOOL_SCOPES", "ExternalReadTools", "WorldReader"]
