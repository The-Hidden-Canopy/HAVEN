"""ExternalReadTools: the admitted, scope-checked read path (WP2/WP3).

Mirrors the gateway's action-path discipline for reads: `admit()` decides
who the caller is, the per-tool method re-checks the scope the tool actually
needs (defense in depth, spec 8.2), and `call()` refuses mis-built requests
(unknown tool, wrong declared scope) before admission is even attempted.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import (
    AdmittedRequest,
    ExternalAgentConnection,
    ExternalProvenance,
    ExternalProvider,
    ExternalRequest,
    ExternalScope,
    PrincipalBinding,
)
from haven.external_agents.errors import INVALID_ARGUMENTS, SCOPE_MISSING, ExternalDenied
from haven.external_agents.gateway import ExternalAgentGateway, ExternalAgentService
from haven.external_agents.reads import READ_TOOL_SCOPES, ExternalReadTools
from haven.external_agents.store import ExternalAgentStore
from haven.web.server import make_server

HOUSEHOLD = "household-1"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class _StubReader:
    """Records calls; returns a fixed world payload."""

    def __init__(self):
        self.calls = 0

    def state(self):
        self.calls += 1
        return {
            "rooms": [{"id": "office", "devices": [], "people": ["Gerron"]}],
            "people": [{"person_id": "gerron", "room": "office"}],
        }


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ExternalAgentStore(Path(tmp) / "external_agents.db")


def _gateway(store, *, resolve_principal=None):
    return ExternalAgentGateway(
        store=store,
        household_id=HOUSEHOLD,
        resolve_principal=resolve_principal or (lambda _pid: None),
        clock=lambda: NOW,
    )


def _connection(store, **overrides):
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


def _request(**overrides) -> ExternalRequest:
    defaults = dict(
        connection_id="extconn-1",
        tool="haven.world.get",
        required_scope=ExternalScope.WORLD_READ,
        external_request_id="mcp_rpc_1",
    )
    defaults.update(overrides)
    return ExternalRequest(**defaults)


def _admitted_with_scopes(store, scopes: frozenset[ExternalScope]) -> AdmittedRequest:
    connection = _connection(store)
    provenance = ExternalProvenance(
        provider=connection.provider,
        connection_id=connection.connection_id,
        external_request_id="req-1",
        correlation_id="corr-1",
        tool="haven.world.get",
        received_at=NOW,
    )
    principal = Principal(
        actor_id=f"external:{connection.connection_id}",
        household_id=connection.household_id,
        role_tier=RoleTier.GUEST,
    )
    return AdmittedRequest(connection=connection, principal=principal, scopes=scopes, provenance=provenance)


def test_world_get_returns_the_world_state(store):
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    result = tools.world_get(_admitted_with_scopes(store, frozenset({ExternalScope.WORLD_READ})))

    assert result["ok"] is True
    assert result["state"]["rooms"][0]["id"] == "office"
    assert set(result["state"]) == {
        "revision",
        "observed_at",
        "rooms",
        "presence",
        "contexts",
        "devices",
        "freshness",
    }


def test_rooms_list_returns_only_the_rooms(store):
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    result = tools.rooms_list(_admitted_with_scopes(store, frozenset({ExternalScope.WORLD_READ})))

    assert result == {"ok": True, "rooms": [{"id": "office", "devices": [], "people": ["Gerron"]}]}


def test_a_read_without_world_read_scope_never_touches_the_reader(store):
    reader = _StubReader()
    tools = ExternalReadTools(gateway=_gateway(store), reader=reader)
    admitted = _admitted_with_scopes(store, frozenset({ExternalScope.KNOWLEDGE_READ}))

    with pytest.raises(ExternalDenied) as exc:
        tools.world_get(admitted)
    assert exc.value.code == SCOPE_MISSING
    with pytest.raises(ExternalDenied) as exc:
        tools.rooms_list(admitted)
    assert exc.value.code == SCOPE_MISSING
    assert reader.calls == 0


def test_call_dispatches_through_admission(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    result = tools.call(_request(tool="haven.rooms.list"))

    assert result["ok"] is True
    assert result["rooms"][0]["id"] == "office"


def test_call_refuses_a_caller_missing_the_tools_scope(store):
    _connection(store, unbound_scopes=frozenset())  # connected, but nothing granted
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    with pytest.raises(ExternalDenied) as exc:
        tools.call(_request())

    assert exc.value.code == SCOPE_MISSING


def test_call_with_a_bound_subject_reads_as_that_principal(store):
    _connection(store)
    store.save_binding(
        PrincipalBinding(
            binding_id="bind-1",
            connection_id="extconn-1",
            subject_key="subj-1",
            subject_label="Gerron's voice",
            principal_id="person-gerron",
            scopes=frozenset({ExternalScope.WORLD_READ}),
            created_by="owner-1",
            created_at=NOW,
        )
    )
    resident = Principal(actor_id="person-gerron", household_id=HOUSEHOLD, role_tier=RoleTier.OWNER)
    gateway = _gateway(store, resolve_principal={"person-gerron": resident}.get)
    tools = ExternalReadTools(gateway=gateway, reader=_StubReader())

    result = tools.call(_request(subject="subj-1"))

    assert result["ok"] is True


def test_call_refuses_an_unknown_tool_before_admission(store):
    # The store is empty: if admission were attempted it would deny with
    # CONNECTION_UNKNOWN, so an INVALID_ARGUMENTS denial proves ordering.
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    with pytest.raises(ExternalDenied) as exc:
        tools.call(_request(tool="haven.world.delete"))

    assert exc.value.code == INVALID_ARGUMENTS


def test_call_refuses_a_misdeclared_required_scope_before_admission(store):
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    with pytest.raises(ExternalDenied) as exc:
        tools.call(_request(tool="haven.world.get", required_scope=ExternalScope.KNOWLEDGE_READ))

    assert exc.value.code == INVALID_ARGUMENTS


def test_call_dispatches_every_registered_read_tool(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))
    tools = ExternalReadTools(gateway=_gateway(store), reader=_StubReader())

    for tool, scope in READ_TOOL_SCOPES.items():
        result = tools.call(_request(tool=tool, required_scope=scope))

        assert result["ok"] is True, tool


def test_server_composition_wires_the_read_tools_to_the_live_director():
    """No transport exists yet, but the composed server must already expose a
    working read path: a connection managed through the owner-facing service
    admits a request, and the reader is the real director's state."""

    with tempfile.TemporaryDirectory() as tmp:
        server, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        try:
            connection = server.external_agents.create_connection(
                provider=ExternalProvider.ALEXA_PLUS,
                display_name="Alexa+",
                created_by=director.owner.actor_id,
                unbound_scopes=frozenset({ExternalScope.WORLD_READ}),
                enabled=True,
            )

            result = server.external_agent_reads.call(
                ExternalRequest(
                    connection_id=connection.connection_id,
                    tool="haven.rooms.list",
                    required_scope=ExternalScope.WORLD_READ,
                    external_request_id="mcp_rpc_1",
                )
            )

            assert result["ok"] is True
            assert {room["id"] for room in result["rooms"]} == {
                room["id"] for room in director.state()["rooms"]
            }
        finally:
            server.server_close()


def test_server_world_read_does_not_export_internal_application_state():
    with tempfile.TemporaryDirectory() as tmp:
        server, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        try:
            connection = server.external_agents.create_connection(
                provider=ExternalProvider.ALEXA_PLUS,
                display_name="Alexa+",
                created_by=director.owner.actor_id,
                unbound_scopes=frozenset({ExternalScope.WORLD_READ}),
                enabled=True,
            )

            result = server.external_agent_reads.call(
                ExternalRequest(
                    connection_id=connection.connection_id,
                    tool="haven.world.get",
                    required_scope=ExternalScope.WORLD_READ,
                    external_request_id="mcp_rpc_2",
                )
            )

            assert set(result["state"]) == {
                "revision",
                "observed_at",
                "rooms",
                "presence",
                "contexts",
                "devices",
                "freshness",
            }
            assert not {"memory", "activity", "conversation", "automations", "scheduler", "system", "voice"} & set(
                result["state"]
            )
        finally:
            server.server_close()
