"""Protocol-neutral records for external-agent admission.

An external agent (Alexa+ over MCP today; any future MCP client, vehicle
assistant, or local agent later) is a *requester*, never an executor. These
values describe who is connected, which HAVEN principal an external subject
is explicitly bound to, which external scopes that binding grants, and the
immutable provenance every admitted request carries into receipts.

Nothing here decides whether an action may run. Authorization is an
intersection: connection state, binding, HAVEN role, external scope, and
then the existing authority engine for the specific action -- a permissive
factor never compensates for a failing one (Build, Ship, Shape spec 8.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping

from haven.core.domain import Principal
from haven.core.time import require_aware_utc


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class ExternalProvider(str, Enum):
    """Which kind of external system a connection represents."""

    ALEXA_PLUS = "alexa_plus"
    MCP_CLIENT = "mcp_client"


class ExternalScope(str, Enum):
    """What an external channel may *request*.

    A scope never says HAVEN policy must allow the operation -- only that
    this channel may ask. The bound principal's role and the authority
    engine still decide.
    """

    WORLD_READ = "world.read"
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_CORRECT = "knowledge.correct"
    ACTIONS_REQUEST = "actions.request"
    AUTOMATIONS_PROPOSE = "automations.propose"
    AUTOMATIONS_APPROVE = "automations.approve"
    HISTORY_READ = "history.read"
    CONNECTIONS_MANAGE = "connections.manage"


# Scopes an owner may grant to *unbound* subjects of a connection. Reads
# only: an unbound session is "a session exists", not "a verified person is
# speaking", so it can never mutate, approve, correct, or read history.
UNBOUND_GRANTABLE_SCOPES = frozenset({ExternalScope.WORLD_READ, ExternalScope.KNOWLEDGE_READ})

# Scopes whose grant deserves an explicit warning in any permission editor.
MUTATING_SCOPES = frozenset(
    {
        ExternalScope.KNOWLEDGE_CORRECT,
        ExternalScope.ACTIONS_REQUEST,
        ExternalScope.AUTOMATIONS_PROPOSE,
        ExternalScope.AUTOMATIONS_APPROVE,
        ExternalScope.CONNECTIONS_MANAGE,
    }
)

_ASSISTANT = frozenset(
    {
        ExternalScope.WORLD_READ,
        ExternalScope.KNOWLEDGE_READ,
        ExternalScope.ACTIONS_REQUEST,
        ExternalScope.AUTOMATIONS_PROPOSE,
        ExternalScope.HISTORY_READ,
    }
)

# Named presets (spec Appendix B). A UI always shows the individual scopes
# before saving; a preset is a starting point, never an opaque grant.
SCOPE_PRESETS: Mapping[str, frozenset[ExternalScope]] = {
    "read_only": frozenset({ExternalScope.WORLD_READ, ExternalScope.KNOWLEDGE_READ}),
    "assistant": _ASSISTANT,
    "trusted_resident": _ASSISTANT | {ExternalScope.KNOWLEDGE_CORRECT},
    "owner_external": _ASSISTANT | {ExternalScope.KNOWLEDGE_CORRECT, ExternalScope.AUTOMATIONS_APPROVE},
}


def parse_scopes(values: object) -> frozenset[ExternalScope]:
    """Parse a caller-supplied scope list against the fixed enum; unknown values fail."""

    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("scopes must be a list of scope names")
    parsed: set[ExternalScope] = set()
    for value in values:
        if isinstance(value, ExternalScope):
            parsed.add(value)
            continue
        try:
            parsed.add(ExternalScope(value))
        except ValueError:
            raise ValueError(f"unknown external scope: {value!r}") from None
    return frozenset(parsed)


@dataclass(frozen=True)
class ExternalAgentConnection:
    """One configured external system. Holds a credential *hash*, never a secret."""

    connection_id: str
    household_id: str
    provider: ExternalProvider
    display_name: str
    enabled: bool
    created_at: datetime
    created_by: str
    credential_hash: str | None = None
    unbound_scopes: frozenset[ExternalScope] = frozenset()
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("connection_id", "household_id", "display_name", "created_by"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name=name))
        if not isinstance(self.provider, ExternalProvider):
            object.__setattr__(self, "provider", ExternalProvider(self.provider))
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        if self.revoked_at is not None:
            object.__setattr__(self, "revoked_at", require_aware_utc(self.revoked_at, name="revoked_at"))
        unbound = frozenset(self.unbound_scopes)
        if not unbound <= UNBOUND_GRANTABLE_SCOPES:
            raise ValueError("unbound subjects may only be granted read scopes")
        object.__setattr__(self, "unbound_scopes", unbound)

    @property
    def active(self) -> bool:
        return self.enabled and self.revoked_at is None


@dataclass(frozen=True)
class PrincipalBinding:
    """An owner's explicit decision that one external subject acts as one HAVEN person."""

    binding_id: str
    connection_id: str
    subject_key: str
    subject_label: str
    principal_id: str
    scopes: frozenset[ExternalScope]
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("binding_id", "connection_id", "subject_key", "subject_label", "principal_id", "created_by"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name=name))
        object.__setattr__(self, "scopes", frozenset(self.scopes))
        if any(not isinstance(scope, ExternalScope) for scope in self.scopes):
            raise ValueError("binding scopes must be ExternalScope values")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at, name="expires_at"))
        if self.revoked_at is not None:
            object.__setattr__(self, "revoked_at", require_aware_utc(self.revoked_at, name="revoked_at"))

    def is_valid_at(self, at: datetime) -> bool:
        at = require_aware_utc(at, name="binding check time")
        if self.revoked_at is not None and self.revoked_at <= at:
            return False
        return self.expires_at is None or at < self.expires_at


@dataclass(frozen=True)
class ExternalRequest:
    """What a transport adapter hands the gateway after protocol validation.

    `subject` is the stable identity the provider asserted (or None when the
    transport has none); `subject_label` is display text only and is never
    used as a binding key. Nothing in here carries role or principal
    claims -- those derive from durable bindings alone.
    """

    connection_id: str
    tool: str
    required_scope: ExternalScope
    external_request_id: str
    subject: str | None = None
    subject_label: str | None = None
    protocol_session_id: str | None = None
    client_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("connection_id", "tool", "external_request_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name=name))
        if not isinstance(self.required_scope, ExternalScope):
            raise ValueError("required_scope must be an ExternalScope")
        if self.subject is not None:
            object.__setattr__(self, "subject", _require_text(self.subject, name="subject"))


@dataclass(frozen=True)
class ExternalProvenance:
    """Immutable, server-derived source metadata for one admitted request.

    Built by the gateway from the durable connection/binding records --
    never from caller-supplied fields -- so a client cannot rewrite or
    suppress its own attribution.
    """

    provider: ExternalProvider
    connection_id: str
    external_request_id: str
    correlation_id: str
    tool: str
    received_at: datetime
    subject_ref: str | None = None
    principal_id: str | None = None
    binding_id: str | None = None
    protocol_session_id: str | None = None
    client_metadata: tuple[tuple[str, str], ...] = ()

    def receipt_block(self) -> tuple[tuple[str, str], ...]:
        """The redacted `external_source` block attached to receipts.

        The subject appears only as its hash reference; the protocol session
        id and client metadata are correlation aids and stay out of durable
        receipts.
        """

        items = [
            ("provider", self.provider.value),
            ("connection_id", self.connection_id),
            ("tool", self.tool),
            ("external_request_id", self.external_request_id),
            ("correlation_id", self.correlation_id),
        ]
        for key in ("subject_ref", "binding_id", "principal_id"):
            value = getattr(self, key)
            if value is not None:
                items.append((key, value))
        return tuple(items)


@dataclass(frozen=True)
class AdmittedRequest:
    """The gateway's answer to "who is this, and what may they ask for"."""

    connection: ExternalAgentConnection
    principal: Principal
    scopes: frozenset[ExternalScope]
    provenance: ExternalProvenance
    binding: PrincipalBinding | None = None

    @property
    def bound(self) -> bool:
        return self.binding is not None


__all__ = [
    "AdmittedRequest",
    "ExternalAgentConnection",
    "ExternalProvenance",
    "ExternalProvider",
    "ExternalRequest",
    "ExternalScope",
    "MUTATING_SCOPES",
    "PrincipalBinding",
    "SCOPE_PRESETS",
    "UNBOUND_GRANTABLE_SCOPES",
    "parse_scopes",
]
