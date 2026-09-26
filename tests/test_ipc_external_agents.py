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
