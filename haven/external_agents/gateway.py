"""ExternalAgentGateway: the one place an external request becomes admitted.

Protocol-neutral (Build/Ship/Shape spec ADR-001): this module knows nothing
about MCP, HTTP, or Alexa. A transport adapter validates its own protocol
envelope and hands `admit()` an `ExternalRequest`; this module decides only
whether the caller may proceed at all, and if so, as which HAVEN principal
and with which external scopes. It never decides whether a *specific
action* is allowed -- that is the existing authority engine's job, reached
only after admission (ADR-002: Alexa is a requester, not an executor).

Authorization is an intersection, not a union (spec 8.2): connection state,
binding validity, and external-scope membership are all necessary and none
sufficient. A scope says the channel *may ask*; it never manufactures a
household role the bound principal does not already have -- so this
gateway does not special-case role-gated scopes like automations.approve
or connections.manage. The existing owner-only checks in
`HavenApplication`/`runtime` already refuse those for a non-owner
principal, external or not, exactly as they do today for a local caller
(non-goal: "Creating a second policy language for MCP").
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Protocol

from haven.core.domain import Principal, RoleTier

from .domain import (
    AdmittedRequest,
    ExternalAgentConnection,
    ExternalProvenance,
    ExternalRequest,
    ExternalScope,
    PrincipalBinding,
)
from .errors import (
    CONNECTION_DISABLED,
    CONNECTION_UNKNOWN,
    CROSS_HOUSEHOLD,
    PRINCIPAL_UNAVAILABLE,
    SCOPE_MISSING,
    ExternalDenied,
)
from .store import ExternalAgentStore

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

_ANONYMOUS_PREFIX = "external"


class PrincipalResolver(Protocol):
    """What the gateway needs to turn a bound `principal_id` into a live `Principal`.

    `HavenApplication.principal_for` already satisfies this shape. The
    gateway depends on this Protocol, never on `haven.web`, so it stays
    reusable by any future household composition (ADR-001).
    """

    def __call__(self, principal_id: str) -> Principal | None: ...


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def anonymous_external_principal(connection: ExternalAgentConnection) -> Principal:
    """The powerless placeholder identity for an unbound external subject.

    GUEST is HAVEN's lowest role tier -- appropriate for a caller whose
    identity a household member has not vouched for. The actor_id carries
    an unforgeable `external:` prefix plus the connection id, so it can
    never collide with (or be mistaken for) a real declared person.
    """

    return Principal(
        actor_id=f"{_ANONYMOUS_PREFIX}:{connection.connection_id}",
        household_id=connection.household_id,
        role_tier=RoleTier.GUEST,
    )


class ExternalAgentGateway:
    """Admits external requests; never executes anything itself."""

    def __init__(
        self,
        *,
        store: ExternalAgentStore,
        household_id: str,
        resolve_principal: PrincipalResolver,
        clock: Callable[[], datetime] = _DEFAULT_CLOCK,
    ) -> None:
        self._store = store
        self._household_id = household_id
        self._resolve_principal = resolve_principal
        self._clock = clock

    def admit(self, request: ExternalRequest, *, correlation_id: str | None = None) -> AdmittedRequest:
        now = self._clock()
        connection = self._store.get_connection(request.connection_id)
        if connection is None:
            raise ExternalDenied(CONNECTION_UNKNOWN, f"unknown connection: {request.connection_id}")
        if connection.household_id != self._household_id:
            self._record_security_event(
                "cross_household", connection=connection, request=request, at=now
            )
            raise ExternalDenied(CROSS_HOUSEHOLD, "connection does not belong to this household")
        if not connection.active:
            raise ExternalDenied(CONNECTION_DISABLED, "connection is disabled or revoked")

        subject_key = request.subject
        binding = self._store.active_binding(connection.connection_id, subject_key) if subject_key else None
        if subject_key is not None:
            self._store.observe_subject(
                connection.connection_id, subject_key, request.subject_label or subject_key, now
            )
        if binding is not None and not binding.is_valid_at(now):
            binding = None

        if binding is None:
            principal = anonymous_external_principal(connection)
            scopes = connection.unbound_scopes
            if request.required_scope not in scopes:
                raise ExternalDenied(
                    SCOPE_MISSING,
                    f"scope not granted to an unbound caller: {request.required_scope.value}",
                )
        else:
            if request.required_scope not in binding.scopes:
                raise ExternalDenied(
                    SCOPE_MISSING, f"binding does not grant scope: {request.required_scope.value}"
                )
            principal = self._resolve_principal(binding.principal_id)
            if principal is None:
                raise ExternalDenied(
                    PRINCIPAL_UNAVAILABLE, "bound principal no longer exists in this household"
                )
            scopes = binding.scopes

        provenance = ExternalProvenance(
            provider=connection.provider,
            connection_id=connection.connection_id,
            external_request_id=request.external_request_id,
            correlation_id=correlation_id or _new_id("corr"),
            tool=request.tool,
            received_at=now,
            subject_ref=subject_key,
            principal_id=principal.actor_id,
            binding_id=binding.binding_id if binding is not None else None,
            protocol_session_id=request.protocol_session_id,
            client_metadata=tuple(sorted(request.client_metadata.items())),
        )
        return AdmittedRequest(
            connection=connection, principal=principal, scopes=scopes, provenance=provenance, binding=binding
        )

    def _record_security_event(
        self, kind: str, *, connection: ExternalAgentConnection, request: ExternalRequest, at: datetime
    ) -> None:
        self._store.append_audit(
            audit_id=_new_id("audit"),
            household_id=connection.household_id,
            kind=kind,
            occurred_at=at,
            connection_id=connection.connection_id,
            detail={"tool": request.tool, "external_request_id": request.external_request_id},
            security=True,
        )


class ExternalAgentService:
    """Owner-facing connection/binding management (spec 28.1's ExternalAgentService).

    Every mutation is audited. Revocation never deletes a row: historical
    receipts keep the provenance that existed at action time (spec 8.6).
    """

    def __init__(self, *, store: ExternalAgentStore, household_id: str, clock: Callable[[], datetime] = _DEFAULT_CLOCK) -> None:
        self._store = store
        self._household_id = household_id
        self._clock = clock

    def list_connections(self) -> tuple[ExternalAgentConnection, ...]:
        return self._store.list_connections(self._household_id)

    def create_connection(
        self,
        *,
        provider,
        display_name: str,
        created_by: str,
        credential_hash: str | None = None,
        unbound_scopes: frozenset[ExternalScope] = frozenset(),
        enabled: bool = False,
    ) -> ExternalAgentConnection:
        now = self._clock()
        connection = ExternalAgentConnection(
            connection_id=_new_id("extconn"),
            household_id=self._household_id,
            provider=provider,
            display_name=display_name,
            enabled=enabled,
            created_at=now,
            created_by=created_by,
            credential_hash=credential_hash,
            unbound_scopes=unbound_scopes,
        )
        self._store.save_connection(connection)
        self._audit("connection.created", actor=created_by, connection_id=connection.connection_id, at=now)
        return connection

    def set_connection_enabled(self, connection_id: str, enabled: bool, *, actor: str) -> ExternalAgentConnection:
        connection = self._require_connection(connection_id)
        updated = replace(connection, enabled=enabled)
        self._store.save_connection(updated)
        self._audit(
            "connection.enabled" if enabled else "connection.disabled",
            actor=actor,
            connection_id=connection_id,
            at=self._clock(),
        )
        return updated

    def revoke_connection(self, connection_id: str, *, actor: str) -> ExternalAgentConnection:
        connection = self._require_connection(connection_id)
        now = self._clock()
        updated = replace(connection, enabled=False, revoked_at=now)
        self._store.save_connection(updated)
        self._audit("connection.revoked", actor=actor, connection_id=connection_id, at=now)
        return updated

    def list_bindings(self, connection_id: str, *, include_revoked: bool = False) -> tuple[PrincipalBinding, ...]:
        return self._store.list_bindings(connection_id, include_revoked=include_revoked)

    def observed_subjects(self, connection_id: str) -> tuple[dict[str, str], ...]:
        return self._store.observed_subjects(connection_id)

    def upsert_binding(
        self,
        *,
        connection_id: str,
        subject_key: str,
        subject_label: str,
        principal_id: str,
        scopes: frozenset[ExternalScope],
        created_by: str,
        expires_at: datetime | None = None,
    ) -> PrincipalBinding:
        self._require_connection(connection_id)
        now = self._clock()
        binding = PrincipalBinding(
            binding_id=_new_id("bind"),
            connection_id=connection_id,
            subject_key=subject_key,
            subject_label=subject_label,
            principal_id=principal_id,
            scopes=scopes,
            created_by=created_by,
            created_at=now,
            expires_at=expires_at,
        )
        self._store.save_binding(binding)
        self._audit(
            "binding.created",
            actor=created_by,
            connection_id=connection_id,
            at=now,
            detail={"binding_id": binding.binding_id, "principal_id": principal_id},
        )
        return binding

    def revoke_binding(self, binding_id: str, *, actor: str) -> PrincipalBinding:
        binding = self._store.get_binding(binding_id)
        if binding is None:
            raise ExternalDenied(CONNECTION_UNKNOWN, f"unknown binding: {binding_id}")
        now = self._clock()
        updated = replace(binding, revoked_at=now)
        self._store.save_binding(updated)
        self._audit(
            "binding.revoked",
            actor=actor,
            connection_id=binding.connection_id,
            at=now,
            detail={"binding_id": binding_id},
        )
        return updated

    def audit(self, *, connection_id: str | None = None, limit: int = 50) -> tuple[dict, ...]:
        return self._store.audit(self._household_id, connection_id=connection_id, limit=limit)

    def _require_connection(self, connection_id: str) -> ExternalAgentConnection:
        connection = self._store.get_connection(connection_id)
        if connection is None or connection.household_id != self._household_id:
            raise ExternalDenied(CONNECTION_UNKNOWN, f"unknown connection: {connection_id}")
        return connection

    def _audit(
        self, kind: str, *, actor: str, connection_id: str, at: datetime, detail: dict | None = None
    ) -> None:
        self._store.append_audit(
            audit_id=_new_id("audit"),
            household_id=self._household_id,
            kind=kind,
            occurred_at=at,
            connection_id=connection_id,
            actor=actor,
            detail=detail,
        )


__all__ = [
    "ExternalAgentGateway",
    "ExternalAgentService",
    "PrincipalResolver",
    "anonymous_external_principal",
]
