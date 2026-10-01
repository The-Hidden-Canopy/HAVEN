"""Web REST twin of the external-agent management surface (Build/Ship/Shape WP6).

The `/api/external-agents/*` routes call the exact handler functions the
native IPC dispatcher serves (`HavenWebServer._external_agents_handlers`), so
these tests mirror `test_ipc_external_agents.py` over real HTTP: same
envelopes, same validation, plus the HTTP-only concerns (status codes,
unknown routes, audit query parsing) and the `external_agents.changed`
invalidation a web-originated mutation owes native clients.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from haven.web.server import make_server

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
STATIC_ROOT = Path(__file__).parents[1] / "haven" / "web" / "static"


@contextmanager
def _boot():
    with tempfile.TemporaryDirectory() as tmp:
        server, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], server, director
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def _request(port: int, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _create_connection(port: int, **overrides) -> dict:
    payload = {"provider": "alexa_plus", "display_name": "Alexa+"}
    payload.update(overrides)
    status, body = _request(port, "POST", "/api/external-agents/connections", payload)
    assert status == 200, body
    assert body["ok"] is True
    return body["connection"]


def test_connections_list_starts_empty() -> None:
    with _boot() as (port, _server, _director):
        status, body = _request(port, "GET", "/api/external-agents/connections")

        assert status == 200
        assert body == {"ok": True, "connections": []}


def test_create_connection_defaults_to_disabled_with_no_secrets_exposed() -> None:
    with _boot() as (port, _server, _director):
        connection = _create_connection(port)

        assert connection["provider"] == "alexa_plus"
        assert connection["enabled"] is False
        assert "credential_hash" not in connection

        status, body = _request(port, "GET", "/api/external-agents/connections")
        assert [c["connection_id"] for c in body["connections"]] == [connection["connection_id"]]


def test_create_connection_rejects_an_unknown_provider() -> None:
    with _boot() as (port, _server, _director):
        status, body = _request(
            port, "POST", "/api/external-agents/connections", {"provider": "not_a_real_provider", "display_name": "X"}
        )

        assert status == 400
        assert body["ok"] is False
        assert "provider" in body["error"]


def test_create_connection_rejects_a_missing_display_name() -> None:
    with _boot() as (port, _server, _director):
        status, body = _request(port, "POST", "/api/external-agents/connections", {"provider": "alexa_plus"})

        assert status == 400
        assert body["ok"] is False
        assert "display_name" in body["error"]


def test_enable_and_revoke_connection_round_trip() -> None:
    with _boot() as (port, _server, _director):
        created = _create_connection(port)
        base = f"/api/external-agents/connections/{created['connection_id']}"

        status, body = _request(port, "POST", f"{base}/enable", {"enabled": True})
        assert status == 200
        assert body["connection"]["enabled"] is True

        status, body = _request(port, "POST", f"{base}/revoke", {})
        assert status == 200
        assert body["connection"]["enabled"] is False
        assert body["connection"]["active"] is False
        assert body["connection"]["revoked_at"] is not None


def test_enable_unknown_connection_is_a_business_failure_not_a_crash() -> None:
    with _boot() as (port, _server, _director):
        status, body = _request(port, "POST", "/api/external-agents/connections/nope/enable", {"enabled": True})

        assert status == 400
        assert body["ok"] is False
        assert "unknown connection" in body["error"]


def test_binding_upsert_requires_a_known_household_principal() -> None:
    with _boot() as (port, _server, _director):
        created = _create_connection(port)

        status, body = _request(
            port,
            "POST",
            f"/api/external-agents/connections/{created['connection_id']}/bindings",
            {
                "subject_key": "subj-1",
                "subject_label": "Gerron's voice",
                "principal_id": "not-a-real-person",
                "scopes": ["world.read"],
            },
        )

        assert status == 400
        assert "unknown household principal" in body["error"]


def test_binding_upsert_and_list_and_revoke_round_trip() -> None:
    with _boot() as (port, _server, director):
        created = _create_connection(port)
        owner_id = director.owner.actor_id
        base = f"/api/external-agents/connections/{created['connection_id']}"

        status, body = _request(
            port,
            "POST",
            f"{base}/bindings",
            {
                "subject_key": "subj-1",
                "subject_label": "Owner voice",
                "principal_id": owner_id,
                "scopes": ["world.read", "actions.request"],
            },
        )
        assert status == 200
        binding = body["binding"]
        assert binding["principal_id"] == owner_id
        assert set(binding["scopes"]) == {"world.read", "actions.request"}

        status, body = _request(port, "GET", f"{base}/bindings")
        assert status == 200
        assert [b["binding_id"] for b in body["bindings"]] == [binding["binding_id"]]

        status, body = _request(port, "POST", f"/api/external-agents/bindings/{binding['binding_id']}/revoke", {})
        assert status == 200
        assert body["binding"]["revoked_at"] is not None

        status, body = _request(port, "GET", f"{base}/bindings")
        assert body["bindings"] == []


def test_binding_upsert_rejects_an_unknown_scope() -> None:
    with _boot() as (port, _server, director):
        created = _create_connection(port)

        status, body = _request(
            port,
            "POST",
            f"/api/external-agents/connections/{created['connection_id']}/bindings",
            {
                "subject_key": "subj-1",
                "subject_label": "Owner voice",
                "principal_id": director.owner.actor_id,
                "scopes": ["owner.role"],
            },
        )

        assert status == 400
        assert "unknown external scope" in body["error"]


def test_observed_subjects_listing() -> None:
    with _boot() as (port, _server, _director):
        created = _create_connection(port)

        status, body = _request(
            port, "GET", f"/api/external-agents/connections/{created['connection_id']}/observed-subjects"
        )

        assert status == 200
        assert body == {"ok": True, "subjects": []}


def test_audit_reflects_management_actions_and_parses_query_params() -> None:
    with _boot() as (port, _server, _director):
        created = _create_connection(port)

        status, body = _request(port, "GET", "/api/external-agents/audit")
        assert status == 200
        kinds = [row["kind"] for row in body["audit"]]
        assert "connection.created" in kinds

        status, body = _request(
            port, "GET", f"/api/external-agents/audit?connection_id={created['connection_id']}&limit=5"
        )
        assert status == 200
        assert all(row["connection_id"] == created["connection_id"] for row in body["audit"])

        status, body = _request(port, "GET", "/api/external-agents/audit?limit=not-a-number")
        assert status == 400
        assert body["ok"] is False


def test_unknown_routes_under_the_prefix_404() -> None:
    with _boot() as (port, _server, _director):
        status, _ = _request(port, "POST", "/api/external-agents/nope", {})
        assert status == 404

        status, _ = _request(port, "GET", "/api/external-agents/connections/some-id/enable")
        assert status == 404


def test_the_transport_entry_point_is_never_exposed_over_http() -> None:
    """`external_agents.tools.call` is IPC-only: a local web session is the
    trusted owner surface and must never drive the external gateway as an
    external connection (that path belongs to the MCP transport alone)."""

    with _boot() as (port, _server, _director):
        status, _ = _request(
            port,
            "POST",
            "/api/external-agents/tools/call",
            {"credential": "x", "tool": "haven.world.get", "external_request_id": "r1"},
        )

        assert status == 404


def test_web_mutation_emits_external_agents_changed() -> None:
    with _boot() as (port, server, _director):
        events: list[str] = []
        server._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

        _create_connection(port)

        assert "external_agents.changed" in events


def test_web_reads_never_emit_external_agents_changed() -> None:
    with _boot() as (port, server, _director):
        events: list[str] = []
        server._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

        status, body = _request(port, "GET", "/api/external-agents/connections")

        assert status == 200
        assert events == []


def test_web_external_agents_panel_covers_the_owner_management_surface() -> None:
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert 'data-view="external-agents"' in index
    for element_id in (
        "external-agents-list",
        "external-agents-create-form",
        "external-agents-audit-list",
    ):
        assert f'id="{element_id}"' in index
    for route in (
        "/api/external-agents/connections",
        "/api/external-agents/audit?limit=50",
        "/bindings",
        "/observed-subjects",
    ):
        assert route in app
    for marker in ("externalAgentScopePicker", "Manage bindings", "Revoke", "external_agents.changed"):
        assert marker in app
    assert ".external-agent-card" in styles
