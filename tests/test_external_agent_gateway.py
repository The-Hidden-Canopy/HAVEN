"""ExternalAgentGateway.admit(): the boundary between an external caller and HAVEN.

Authorization is an intersection, not a union (spec 8.2) -- these tests
exercise each factor's failure independently: unknown/disabled/foreign
connection, missing/expired/revoked binding, missing scope, and an
unresolvable bound principal. A permissive factor never compensates for a
failing one.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import (
    ExternalAgentConnection,
    ExternalProvider,
    ExternalRequest,
    ExternalScope,
    PrincipalBinding,
)
from haven.external_agents.errors import (
    CONNECTION_DISABLED,
    CONNECTION_UNKNOWN,
    CROSS_HOUSEHOLD,
    PRINCIPAL_UNAVAILABLE,
    SCOPE_MISSING,
    ExternalDenied,
)
from haven.external_agents.gateway import ExternalAgentGateway, ExternalAgentService, anonymous_external_principal
from haven.external_agents.store import ExternalAgentStore

HOUSEHOLD = "household-1"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ExternalAgentStore(Path(tmp) / "external_agents.db")


def _principal_registry(*principals: Principal):
    by_id = {p.actor_id: p for p in principals}
    return by_id.get


def _gateway(store, *, household_id=HOUSEHOLD, resolve_principal=None, now=NOW):
    return ExternalAgentGateway(
        store=store,
        household_id=household_id,
        resolve_principal=resolve_principal or (lambda _pid: None),
        clock=lambda: now,
    )


def _connection(store, **overrides) -> ExternalAgentConnection:
    defaults = dict(
        connection_id="extconn-1",
        household_id=HOUSEHOLD,
        provider=ExternalProvider.ALEXA_PLUS,
        display_name="Alexa+",
        enabled=True,
        created_at=NOW,
        created_by="owner-1",
    )
    defaults.update(overrides)
    connection = ExternalAgentConnection(**defaults)
    store.save_connection(connection)
    return connection


def _binding(store, **overrides) -> PrincipalBinding:
    defaults = dict(
        binding_id="bind-1",
        connection_id="extconn-1",
        subject_key="subj-1",
        subject_label="Gerron's voice",
        principal_id="person-gerron",
        scopes=frozenset({ExternalScope.WORLD_READ}),
        created_by="owner-1",
        created_at=NOW,
    )
    defaults.update(overrides)
    binding = PrincipalBinding(**defaults)
    store.save_binding(binding)
    return binding


def _request(**overrides) -> ExternalRequest:
    defaults = dict(
        connection_id="extconn-1",
        tool="haven.world.get",
        required_scope=ExternalScope.WORLD_READ,
        external_request_id="mcp_rpc_1",
    )
    defaults.update(overrides)
    return ExternalRequest(**defaults)


def test_unknown_connection_is_denied(store):
    gateway = _gateway(store)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request())
    assert exc.value.code == CONNECTION_UNKNOWN


def test_disabled_connection_is_denied(store):
    _connection(store, enabled=False)
    gateway = _gateway(store)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request())
    assert exc.value.code == CONNECTION_DISABLED


def test_revoked_connection_is_denied_even_if_enabled(store):
    _connection(store, enabled=True, revoked_at=NOW - timedelta(minutes=1))
    gateway = _gateway(store)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request())
    assert exc.value.code == CONNECTION_DISABLED


def test_cross_household_connection_is_denied_and_audited(store):
    _connection(store, household_id="other-household")
    gateway = _gateway(store, household_id=HOUSEHOLD)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request())
    assert exc.value.code == CROSS_HOUSEHOLD
    audit_rows = store.audit("other-household")
    assert audit_rows and audit_rows[0]["security"] is True


def test_unbound_subject_with_no_granted_scope_is_denied(store):
    _connection(store, unbound_scopes=frozenset())
    gateway = _gateway(store)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request(required_scope=ExternalScope.WORLD_READ))
    assert exc.value.code == SCOPE_MISSING


def test_unbound_subject_with_granted_scope_is_admitted_as_anonymous(store):
    connection = _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))
    gateway = _gateway(store)

    admitted = gateway.admit(_request(required_scope=ExternalScope.WORLD_READ))

    assert admitted.bound is False
    assert admitted.principal == anonymous_external_principal(connection)
    assert admitted.principal.role_tier == RoleTier.GUEST
    assert admitted.scopes == frozenset({ExternalScope.WORLD_READ})


def test_no_subject_at_all_follows_the_unbound_path(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))
    gateway = _gateway(store)

    admitted = gateway.admit(_request(required_scope=ExternalScope.WORLD_READ))  # no subject supplied

    assert admitted.bound is False


def test_bound_subject_maps_to_the_resolved_principal(store):
    _connection(store)
    _binding(store, scopes=frozenset({ExternalScope.WORLD_READ, ExternalScope.ACTIONS_REQUEST}))
    resident = Principal(actor_id="person-gerron", household_id=HOUSEHOLD, role_tier=RoleTier.OWNER)
    gateway = _gateway(store, resolve_principal=_principal_registry(resident))

    admitted = gateway.admit(_request(subject="subj-1", required_scope=ExternalScope.ACTIONS_REQUEST))

    assert admitted.bound is True
    assert admitted.principal == resident
    assert admitted.binding.binding_id == "bind-1"
    assert admitted.provenance.principal_id == "person-gerron"
    assert admitted.provenance.binding_id == "bind-1"


def test_bound_subject_missing_the_required_scope_is_denied(store):
    _connection(store)
    _binding(store, scopes=frozenset({ExternalScope.WORLD_READ}))
    resident = Principal(actor_id="person-gerron", household_id=HOUSEHOLD, role_tier=RoleTier.OWNER)
    gateway = _gateway(store, resolve_principal=_principal_registry(resident))

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request(subject="subj-1", required_scope=ExternalScope.AUTOMATIONS_APPROVE))
    assert exc.value.code == SCOPE_MISSING


def test_expired_binding_falls_back_to_the_unbound_profile(store):
    _connection(store, unbound_scopes=frozenset())
    _binding(store, expires_at=NOW - timedelta(seconds=1), scopes=frozenset({ExternalScope.ACTIONS_REQUEST}))
    gateway = _gateway(store)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request(subject="subj-1", required_scope=ExternalScope.ACTIONS_REQUEST))
    # Denied for scope_missing (unbound has none granted), not treated as if
    # the expired binding's scopes still applied.
    assert exc.value.code == SCOPE_MISSING


def test_revoked_binding_falls_back_to_the_unbound_profile(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))
    _binding(store, revoked_at=NOW - timedelta(seconds=1), scopes=frozenset({ExternalScope.ACTIONS_REQUEST}))
    gateway = _gateway(store)

    admitted = gateway.admit(_request(subject="subj-1", required_scope=ExternalScope.WORLD_READ))

    assert admitted.bound is False


def test_bound_principal_no_longer_resolvable_is_denied(store):
    _connection(store)
    _binding(store)
    gateway = _gateway(store, resolve_principal=lambda _pid: None)

    with pytest.raises(ExternalDenied) as exc:
        gateway.admit(_request(subject="subj-1", required_scope=ExternalScope.WORLD_READ))
    assert exc.value.code == PRINCIPAL_UNAVAILABLE


def test_admit_records_an_observed_subject(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))
    gateway = _gateway(store)

    gateway.admit(_request(subject="subj-new", subject_label="New voice", required_scope=ExternalScope.WORLD_READ))

    rows = store.observed_subjects("extconn-1")
    assert rows and rows[0]["subject_key"] == "subj-new"


def test_provenance_never_trusts_caller_supplied_principal_claims(store):
    """`ExternalRequest` has no principal/role field at all -- there is nothing
    to ignore, which is the point: the admitted principal can only ever come
    from a durable binding or the anonymous fallback."""

    assert not hasattr(ExternalRequest(**{
        "connection_id": "x", "tool": "t", "required_scope": ExternalScope.WORLD_READ, "external_request_id": "r"
    }), "role")


def test_service_create_connection_defaults_to_disabled(store):
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD, clock=lambda: NOW)

    connection = service.create_connection(
        provider=ExternalProvider.ALEXA_PLUS, display_name="Alexa+", created_by="owner-1"
    )

    assert connection.enabled is False
    assert service.list_connections() == (connection,)


def test_service_set_connection_enabled_is_audited(store):
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD, clock=lambda: NOW)
    connection = service.create_connection(
        provider=ExternalProvider.ALEXA_PLUS, display_name="Alexa+", created_by="owner-1"
    )

    updated = service.set_connection_enabled(connection.connection_id, True, actor="owner-1")

    assert updated.enabled is True
    kinds = [row["kind"] for row in service.audit()]
    assert "connection.enabled" in kinds


def test_service_revoke_connection_disables_and_stamps_revoked_at(store):
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD, clock=lambda: NOW)
    connection = service.create_connection(
        provider=ExternalProvider.ALEXA_PLUS, display_name="Alexa+", created_by="owner-1", enabled=True
    )

    revoked = service.revoke_connection(connection.connection_id, actor="owner-1")

    assert revoked.enabled is False
    assert revoked.revoked_at == NOW
    assert revoked.active is False


def test_service_upsert_and_revoke_binding_round_trip(store):
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD, clock=lambda: NOW)
    connection = service.create_connection(
        provider=ExternalProvider.ALEXA_PLUS, display_name="Alexa+", created_by="owner-1"
    )

    binding = service.upsert_binding(
        connection_id=connection.connection_id,
        subject_key="subj-1",
        subject_label="Gerron's voice",
        principal_id="person-gerron",
        scopes=frozenset({ExternalScope.WORLD_READ}),
        created_by="owner-1",
    )
    assert service.list_bindings(connection.connection_id) == (binding,)

    revoked = service.revoke_binding(binding.binding_id, actor="owner-1")
    assert revoked.revoked_at == NOW
    assert service.list_bindings(connection.connection_id) == ()
    assert service.list_bindings(connection.connection_id, include_revoked=True) == (revoked,)


def test_service_binding_on_unknown_connection_is_denied(store):
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD, clock=lambda: NOW)

    with pytest.raises(ExternalDenied) as exc:
        service.upsert_binding(
            connection_id="nope",
            subject_key="s",
            subject_label="s",
            principal_id="p",
            scopes=frozenset(),
            created_by="owner-1",
        )
    assert exc.value.code == CONNECTION_UNKNOWN


def test_service_scopes_a_connection_from_another_household_as_unknown(store):
    _connection(store, household_id="other-household")
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD, clock=lambda: NOW)

    with pytest.raises(ExternalDenied) as exc:
        service.set_connection_enabled("extconn-1", True, actor="owner-1")
    assert exc.value.code == CONNECTION_UNKNOWN
