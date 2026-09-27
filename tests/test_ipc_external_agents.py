"""Native external-agent management IPC adapter: connections/bindings CRUD.

No MCP transport exists yet (Build/Ship/Shape WP2), so these exercise the
owner-facing management surface only -- the same `ExternalAgentService`
`self.external_agent_gateway` will sit beside once a transport calls
`admit()`.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        try:
            yield instance, director
        finally:
            instance.server_close()


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def test_connections_list_starts_empty(server) -> None:
    instance, _ = server

    response = _dispatch(instance, "external_agents.connections.list", {})

    assert response["ok"] is True
    assert response["result"]["connections"] == []


def test_production_boot_without_a_clock_manages_connections() -> None:
    """`make_server()`'s production callers pass no clock; the external-agent
    services must fall back to wall time, not crash (regression: a None clock
    used to reach `ExternalAgentService._clock` and TypeError on create)."""

    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", demo=True)
        try:
            created = _dispatch(
                instance,
                "external_agents.connections.create",
                {"provider": "alexa_plus", "display_name": "Alexa+"},
            )
            assert created["ok"] is True
            connection_id = created["result"]["connection"]["connection_id"]
            enabled = _dispatch(
                instance,
                "external_agents.connections.enable",
                {"connection_id": connection_id, "enabled": True},
            )
            assert enabled["result"]["connection"]["enabled"] is True
        finally:
            instance.server_close()


def test_create_connection_defaults_to_disabled_with_no_secrets_exposed(server) -> None:
    instance, _ = server

    response = _dispatch(
        instance,
        "external_agents.connections.create",
        {"provider": "alexa_plus", "display_name": "Alexa+"},
    )

    assert response["ok"] is True
    connection = response["result"]["connection"]
    assert connection["provider"] == "alexa_plus"
    assert connection["enabled"] is False
    assert "credential_hash" not in connection


def test_create_connection_rejects_an_unknown_provider(server) -> None:
    instance, _ = server

    response = _dispatch(
        instance, "external_agents.connections.create", {"provider": "not_a_real_provider", "display_name": "X"}
    )

    assert response["ok"] is False
    assert "provider" in response["error"]


def test_create_connection_rejects_missing_display_name(server) -> None:
    instance, _ = server

    response = _dispatch(instance, "external_agents.connections.create", {"provider": "alexa_plus"})

    assert response["ok"] is False
    assert "display_name" in response["error"]


def test_enable_and_revoke_connection_round_trip(server) -> None:
    instance, _ = server
    created = _dispatch(
        instance, "external_agents.connections.create", {"provider": "alexa_plus", "display_name": "Alexa+"}
    )["result"]["connection"]

    enabled = _dispatch(
        instance, "external_agents.connections.enable", {"connection_id": created["connection_id"], "enabled": True}
    )
    assert enabled["result"]["connection"]["enabled"] is True

    revoked = _dispatch(instance, "external_agents.connections.revoke", {"connection_id": created["connection_id"]})
    assert revoked["result"]["connection"]["enabled"] is False
    assert revoked["result"]["connection"]["active"] is False
    assert revoked["result"]["connection"]["revoked_at"] is not None


def test_enable_unknown_connection_is_a_business_failure_not_a_crash(server) -> None:
    instance, _ = server

    response = _dispatch(instance, "external_agents.connections.enable", {"connection_id": "nope"})

    assert response["ok"] is True  # adapter call succeeded
    assert response["result"]["ok"] is False
    assert "unknown connection" in response["result"]["error"]


def test_binding_upsert_requires_a_known_household_principal(server) -> None:
    instance, _ = server
    created = _dispatch(
        instance, "external_agents.connections.create", {"provider": "alexa_plus", "display_name": "Alexa+"}
    )["result"]["connection"]

    response = _dispatch(
        instance,
        "external_agents.bindings.upsert",
        {
            "connection_id": created["connection_id"],
            "subject_key": "subj-1",
            "subject_label": "Gerron's voice",
            "principal_id": "not-a-real-person",
            "scopes": ["world.read"],
        },
    )

    assert response["ok"] is False
    assert "unknown household principal" in response["error"]


def test_binding_upsert_and_list_and_revoke_round_trip(server) -> None:
    instance, director = server
    created = _dispatch(
        instance, "external_agents.connections.create", {"provider": "alexa_plus", "display_name": "Alexa+"}
    )["result"]["connection"]
    owner_id = director.owner.actor_id

    upserted = _dispatch(
        instance,
        "external_agents.bindings.upsert",
        {
            "connection_id": created["connection_id"],
            "subject_key": "subj-1",
            "subject_label": "Owner voice",
            "principal_id": owner_id,
            "scopes": ["world.read", "actions.request"],
        },
    )
    assert upserted["ok"] is True
    binding = upserted["result"]["binding"]
    assert binding["principal_id"] == owner_id
    assert set(binding["scopes"]) == {"world.read", "actions.request"}

    listed = _dispatch(instance, "external_agents.bindings.list", {"connection_id": created["connection_id"]})
    assert [b["binding_id"] for b in listed["result"]["bindings"]] == [binding["binding_id"]]

    revoked = _dispatch(instance, "external_agents.bindings.revoke", {"binding_id": binding["binding_id"]})
    assert revoked["result"]["binding"]["revoked_at"] is not None
    listed_after = _dispatch(instance, "external_agents.bindings.list", {"connection_id": created["connection_id"]})
    assert listed_after["result"]["bindings"] == []


def test_binding_upsert_rejects_an_unknown_scope(server) -> None:
    instance, director = server
    created = _dispatch(
        instance, "external_agents.connections.create", {"provider": "alexa_plus", "display_name": "Alexa+"}
    )["result"]["connection"]

    response = _dispatch(
        instance,
        "external_agents.bindings.upsert",
        {
            "connection_id": created["connection_id"],
            "subject_key": "subj-1",
            "subject_label": "Owner voice",
            "principal_id": director.owner.actor_id,
            "scopes": ["owner.role"],
        },
    )

    assert response["ok"] is False
    assert "unknown external scope" in response["error"]


def test_audit_reflects_management_actions(server) -> None:
    instance, _ = server
    _dispatch(instance, "external_agents.connections.create", {"provider": "alexa_plus", "display_name": "Alexa+"})

    response = _dispatch(instance, "external_agents.audit", {})

    assert response["ok"] is True
    kinds = [row["kind"] for row in response["result"]["audit"]]
    assert "connection.created" in kinds


def test_connection_create_emits_external_agents_changed(server) -> None:
    instance, _ = server
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    _dispatch(instance, "external_agents.connections.create", {"provider": "alexa_plus", "display_name": "Alexa+"})

    assert "external_agents.changed" in events


def test_reads_never_emit_external_agents_changed(server) -> None:
    instance, _ = server
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    _dispatch(instance, "external_agents.connections.list", {})

    assert events == []


# -- external_agents.tools.call: the WP2/WP3 transport entry point ------------

TOOL_CREDENTIAL = "test-bearer-token"


def _tool_connection(instance, director, *, unbound_scopes=None, bind_owner_scopes=None) -> str:
    """Create + enable a credentialed connection; optionally bind the owner. Returns its id."""
    created = _dispatch(
        instance,
        "external_agents.connections.create",
        {
            "provider": "mcp_client",
            "display_name": "MCP client",
            "credential": TOOL_CREDENTIAL,
            "unbound_scopes": unbound_scopes or [],
        },
    )["result"]["connection"]
    connection_id = created["connection_id"]
    if bind_owner_scopes:
        _dispatch(
            instance,
            "external_agents.bindings.upsert",
            {
                "connection_id": connection_id,
                "subject_key": "subj-1",
                "subject_label": "Owner voice",
                "principal_id": director.owner.actor_id,
                "scopes": bind_owner_scopes,
            },
        )
    _dispatch(instance, "external_agents.connections.enable", {"connection_id": connection_id, "enabled": True})
    return connection_id


def _tool_call(instance, **overrides) -> dict:
    params = {
        "credential": TOOL_CREDENTIAL,
        "tool": "haven.world.get",
        "external_request_id": "mcp_rpc_1",
    }
    params.update(overrides)
    return _dispatch(instance, "external_agents.tools.call", params)


def test_create_connection_with_a_credential_never_exposes_it(server) -> None:
    instance, _ = server

    response = _dispatch(
        instance,
        "external_agents.connections.create",
        {"provider": "mcp_client", "display_name": "MCP client", "credential": TOOL_CREDENTIAL},
    )

    assert response["ok"] is True
    connection = response["result"]["connection"]
    assert "credential_hash" not in connection
    assert "credential" not in connection


def test_create_connection_rejects_a_duplicate_credential(server) -> None:
    instance, _ = server
    _dispatch(
        instance,
        "external_agents.connections.create",
        {"provider": "mcp_client", "display_name": "First", "credential": TOOL_CREDENTIAL},
    )

    response = _dispatch(
        instance,
        "external_agents.connections.create",
        {"provider": "mcp_client", "display_name": "Second", "credential": TOOL_CREDENTIAL},
    )

    assert response["ok"] is False
    assert "credential already in use" in response["error"]


def test_tools_call_with_an_unknown_credential_denies_with_a_code(server) -> None:
    instance, _ = server

    response = _tool_call(instance, credential="wrong-token")

    assert response["ok"] is True  # adapter call succeeded
    assert response["result"]["ok"] is False
    assert response["result"]["code"] == "external.connection_unknown"


def test_tools_call_read_tool_round_trip(server) -> None:
    instance, director = server
    _tool_connection(instance, director, unbound_scopes=["world.read"])

    response = _tool_call(instance, tool="haven.rooms.list")

    result = response["result"]
    assert result["ok"] is True
    assert {room["id"] for room in result["rooms"]} == {room["id"] for room in director.state()["rooms"]}


def test_tools_call_action_request_executes_as_the_bound_owner_and_emits(server) -> None:
    instance, director = server
    connection_id = _tool_connection(instance, director, bind_owner_scopes=["actions.request"])
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    response = _tool_call(
        instance,
        tool="haven.action.request",
        subject="subj-1",
        arguments={"device_id": "office_light", "service": "light.turn_off"},
    )

    result = response["result"]
    assert result["ok"] is True
    assert result["status"] == "executed"
    assert "home.state.changed" in events
    assert "authority.pending.changed" in events
    # The receipt carries the external provenance, not a local-resident claim.
    receipt = next(r for r in director.receipts if r.receipt_id == result["receipt_id"])
    assert dict(receipt.external_source).get("connection_id") == connection_id
    assert dict(receipt.external_source).get("tool") == "haven.action.request"


def test_tools_call_action_business_failure_passes_the_envelope_through_without_emitting(server) -> None:
    instance, director = server
    _tool_connection(instance, director, bind_owner_scopes=["actions.request"])
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    response = _tool_call(
        instance,
        tool="haven.action.request",
        subject="subj-1",
        arguments={"device_id": "no-such-device", "service": "light.turn_off"},
    )

    result = response["result"]
    assert result["ok"] is False
    assert result["error"] == "unknown device"
    assert "code" not in result  # business envelope, not a gateway denial
    assert events == []


def test_tools_call_reads_never_emit(server) -> None:
    instance, director = server
    _tool_connection(instance, director, unbound_scopes=["world.read"])
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    _tool_call(instance)

    assert events == []
