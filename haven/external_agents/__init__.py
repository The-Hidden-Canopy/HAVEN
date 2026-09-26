"""External Agent Gateway: HAVEN's admission boundary for outside assistants.

Alexa+ (over MCP) is the first external conversational client; this package
is the protocol-neutral seam every future one shares (Build/Ship/Shape spec
section 7.3, ADR-001). It answers exactly one question -- who is this
caller, and what may they ask for -- and answers it the same way regardless
of transport. It never decides whether a specific action executes: that
remains the existing HAVEN authority engine, reached only after admission
(ADR-002, "Alexa is a requester, not an executor").
"""

from .domain import (
    MUTATING_SCOPES,
    SCOPE_PRESETS,
    UNBOUND_GRANTABLE_SCOPES,
    AdmittedRequest,
    ExternalAgentConnection,
    ExternalProvenance,
    ExternalProvider,
    ExternalRequest,
    ExternalScope,
    PrincipalBinding,
    parse_scopes,
)
from .errors import ExternalDenied
from .gateway import ExternalAgentGateway, ExternalAgentService, PrincipalResolver, anonymous_external_principal
from .store import ExternalAgentStore

__all__ = [
    "AdmittedRequest",
    "ExternalAgentConnection",
    "ExternalAgentGateway",
    "ExternalAgentService",
    "ExternalAgentStore",
    "ExternalDenied",
    "ExternalProvenance",
    "ExternalProvider",
    "ExternalRequest",
    "ExternalScope",
    "MUTATING_SCOPES",
    "PrincipalBinding",
    "PrincipalResolver",
    "SCOPE_PRESETS",
    "UNBOUND_GRANTABLE_SCOPES",
    "anonymous_external_principal",
    "parse_scopes",
]
