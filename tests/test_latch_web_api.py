"""HTTP contract for the Latch WebUI compatibility surface.

``/api/latch/*`` is a thin same-origin adapter over a local Latch daemon:
fixed read paths plus the one idempotent cancellation proposal, and the
shared appearance settings file the native ThemeService owns. These tests
inject a fake transport (no sockets to Latch) and a temp settings file, and
serve real Latch record shapes -- the exact envelopes latchd's
projection/access stores, scheduler, and action engine produce.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from haven.integrations.latch import (
    LatchConfigError,
    LatchPluginService,
    LatchUnavailableError,
)
from haven.web.server import make_server

ROOT = Path(__file__).resolve().parents[1]

# --- Real Latch envelopes (mirrored from latchd's stores) -------------------

COMMITMENTS = {
    "items": [
        {
            "id": "com_streaming_north",
            "kind": "subscription",
            "lifecycle": "active",
            "provider": "North Streaming",
            "subject": None,
            "account": {"type": "email", "id": "person@example.test"},
            "economics": {"amount_minor": 1549, "currency": "USD", "cadence": "monthly"},
            "temporal": {
                "renewal_at": "2026-11-02T00:00:00-07:00",
                "cancel_before": "2026-10-28T00:00:00-07:00",
            },
            "confidence": {"overall": "SUPPORTED"},
            "facts": {"kind": {"value": "subscription", "evidence_id": "ev_1"}},
            "evidence": ["ev_1"],
            "updated_at": "2026-10-01T00:00:00Z",
        },
        {
            "id": "com_cloud_atlas",
            "kind": "subscription",
            "lifecycle": "active",
            "provider": "Atlas Cloud",
            "subject": None,
            "account": {"type": "email", "id": "person@example.test"},
            "economics": {"amount_minor": 12000, "currency": "USD", "cadence": "annual"},
            "temporal": {"renewal_at": "2027-01-15T00:00:00-08:00", "cancel_before": None},
            "confidence": {"overall": "SUPPORTED"},
            "facts": {},
            "evidence": [],
            "updated_at": "2026-10-01T00:00:00Z",
        },
    ]
}

STALE_ACCESS = {
    "items": [
        {
            "id": "acc_1",
            "grantor": {"type": "person", "id": "person@example.test"},
            "grantee": {"type": "person", "id": "person@example.test"},
            "resource": {"type": "service", "id": "north-streaming"},
            "capabilities": ["playback"],
            "granted_at": "2025-08-01T00:00:00Z",
            "expires_at": None,
            "last_used_at": None,
            "last_reviewed_at": None,
            "parent_commitment": "com_streaming_north",
            "source_connector": "demo.connector",
            "source_object_id": "demo-grant-1",
            "stale_notified": False,
            "updated_at": "2026-10-01T00:00:00Z",
            "stale": True,
            "stale_reason": "no recorded use since granted 454 days ago",
        },
        {
            "id": "acc_2",
            "grantor": {"type": "person", "id": "person@example.test"},
            "grantee": {"type": "person", "id": "person@example.test"},
            "resource": {"type": "service", "id": "atlas-cloud"},
            "capabilities": ["storage"],
            "granted_at": "2024-11-01T00:00:00Z",
            "expires_at": None,
            "last_used_at": None,
            "last_reviewed_at": None,
            "parent_commitment": "com_cloud_atlas",
            "source_connector": "demo.connector",
            "source_object_id": "demo-grant-2",
            "stale_notified": False,
            "updated_at": "2026-10-01T00:00:00Z",
            "stale": True,
            "stale_reason": "no recorded use since granted 728 days ago",
        },
    ]
}

FRESH_GRANT = {
    "id": "acc_3",
    "grantor": {"type": "person", "id": "person@example.test"},
    "grantee": {"type": "person", "id": "person@example.test"},
    "resource": {"type": "service", "id": "daily-journal"},
    "capabilities": ["read"],
    "granted_at": "2026-09-01T00:00:00Z",
    "expires_at": None,
    "last_used_at": "2026-09-20T00:00:00Z",
    "last_reviewed_at": None,
    "parent_commitment": None,
    "source_connector": "demo.connector",
    "source_object_id": "demo-grant-3",
    "stale_notified": False,
    "updated_at": "2026-10-01T00:00:00Z",
    "stale": False,
    "stale_reason": None,
}

ALL_ACCESS = {"items": [STALE_ACCESS["items"][0], STALE_ACCESS["items"][1], FRESH_GRANT]}

SCHEDULER_PROPOSALS = {
    "items": [
        {
            "id": "rev_1",
            "schedule_id": "sch_access_review",
            "occurrence_id": "occ_1",
            "kind": "access_review",
            "target": {"access_grant_id": "acc_1"},
            "target_key": "acc_1",
            "reason": "access grant acc_1 is stale",
            "status": "proposed",
            "evidence": [{"evidence_id": "ev_1", "kind": "staleness"}],
            "budgets": {},
            "idempotency_key": "sched:access_review:occ_1",
            "created_at": "2026-10-01T00:00:00Z",
        },
        {
            "id": "rev_2",
            "schedule_id": "sch_renewal_watch",
            "occurrence_id": "occ_2",
            "kind": "commitment_review",
            "target": {"commitment_id": "com_cloud_atlas"},
            "target_key": "com_cloud_atlas",
            "reason": "annual renewal approaching",
            "status": "proposed",
            "evidence": [{"evidence_id": "ev_2", "kind": "renewal"}],
            "budgets": {},
            "idempotency_key": "sched:renewal_watch:occ_2",
            "created_at": "2026-10-01T00:00:00Z",
        },
    ]
}

PROPOSED_PLAN = {
    "plan_id": "act_9",
    "commitment_id": "com_streaming_north",
    "operation": "cancel_subscription",
    "target": {},
    "authority_class": "medium",
    "impact": {"affected": []},
    "status": "proposed",
    "created_at": "2026-10-01T00:00:00Z",
    "updated_at": "2026-10-01T00:00:00Z",
}

ACTION_PLANS = {"items": [PROPOSED_PLAN]}

EVIDENCE_FULL = {
    "id": "ev_1",
    "source_kind": "manual",
    "source_connector": "manual",
    "acquired_at": "2026-09-28T00:00:00Z",
    "content_hash": "sha256:abc123",
    "mime_type": "text/plain",
    "metadata": {},
    "content": "North Streaming subscription renews 2026-11-02.",
}

LATCH_HTML_UI = b"<!doctype html><html><head><title>Latch</title></head><body></body></html>"


class FakeLatchTransport:
    """Scripted stand-in for the stdlib transport; records every request."""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = dict(routes or {})
        self.requests: list[tuple[str, str, bytes | None]] = []
        self.always_fail = False
        self.fail_next = 0

    def __call__(self, method: str, url: str, body: bytes | None, timeout: float):
        self.requests.append((method, url, body))
        if self.always_fail:
            raise LatchUnavailableError("connection refused")
        if self.fail_next > 0:
            self.fail_next -= 1
            raise LatchUnavailableError("connection reset")
        path = urlsplit(url).path
        handler = self.routes.get((method, path))
        if handler is None:
            return (
                404,
                {"Content-Type": "application/json"},
                json.dumps({"error": "no such route"}).encode(),
            )
        if callable(handler):
            handler = handler(method, url, body)
        status, headers, payload = handler
        if isinstance(payload, (dict, list)):
            headers = {"Content-Type": "application/json", **headers}
            payload = json.dumps(payload).encode()
        return status, headers, payload

    def paths(self) -> list[str]:
        return [urlsplit(url).path for _method, url, _body in self.requests]


def _access_route(_method, url, _body):
    # latchd applies the stale_only filter server-side; the fixture mirrors
    # that by inspecting the where= query HAVEN sends.
    return 200, {}, STALE_ACCESS if "stale_only" in url else ALL_ACCESS


def _latch_routes() -> dict:
    return {
        ("GET", "/v1/commitments"): (200, {}, COMMITMENTS),
        ("GET", "/v1/commitments/com_streaming_north"): (200, {}, COMMITMENTS["items"][0]),
        ("GET", "/v1/access"): _access_route,
        ("GET", "/v1/schedules/proposals"): (200, {}, SCHEDULER_PROPOSALS),
        ("GET", "/v1/actions"): (200, {}, ACTION_PLANS),
        ("GET", "/v1/evidence/ev_1"): (200, {}, EVIDENCE_FULL),
    }


def _plan_route() -> dict:
    def propose(_method, _url, body):
        payload = json.loads(body)
        assert payload["operation"] == "cancel_subscription"
        plan = dict(PROPOSED_PLAN)
        plan["commitment_id"] = payload["commitment_id"]
        # Idempotent by key, like latchd: the same key returns one durable plan.
        plan["plan_id"] = "act_" + payload["idempotency_key"].rsplit(":", 1)[-1]
        return 200, {}, plan

    return {("POST", "/v1/actions/plan"): propose}


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(
            0,
            demo=True,
            session_token="synthetic-latch-session",
            data_dir=Path(tmp) / "data",
            models_root=Path(tmp) / "models",
        )
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance, instance.server_address[1]
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)


@pytest.fixture()
def latch(server, tmp_path, monkeypatch):
    instance, _port = server
    monkeypatch.delenv("HAVEN_LATCH_SETTINGS_PATH", raising=False)
    transport = FakeLatchTransport({**_latch_routes(), **_plan_route()})
    service = LatchPluginService(
        transport=transport,
        settings_path=tmp_path / "Haven.Desktop" / "settings.json",
    )
    instance.latch = service
    yield service, transport
    instance.latch = None


def _request(port: int, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload) if payload is not None else ""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers={"Content-Type": "application/json", "Cookie":"haven_session=synthetic-latch-session"})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def _get(port: int, path: str) -> tuple[int, dict]:
    return _request(port, "GET", path)


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    return _request(port, "POST", path, payload)


def _put(port: int, path: str, payload: dict) -> tuple[int, dict]:
    return _request(port, "PUT", path, payload)


# --- configuration ----------------------------------------------------------


def test_service_reads_env_base_url_demo_and_settings_override(tmp_path, monkeypatch):
    settings = tmp_path / "isolated" / "settings.json"
    env = {
        "HAVEN_LATCH_BASE_URL": "http://localhost:9999",
        "HAVEN_LATCH_DEMO": "1",
        "HAVEN_LATCH_SETTINGS_PATH": str(settings),
    }
    service = LatchPluginService(environ=env)
    assert service.base_url == "http://localhost:9999"
    assert service.is_demo is True
    assert service.label == "Synthetic demo"
    assert service.settings_path == settings

    plain = LatchPluginService(environ={})
    assert plain.base_url == "http://127.0.0.1:8787"
    assert plain.is_demo is False
    assert plain.label is None


def test_service_rejects_non_loopback_base_url():
    with pytest.raises(LatchConfigError):
        LatchPluginService(base_url="http://latch.example.com:8787", environ={})


# --- reads over the web surface ----------------------------------------------


def test_commitments_pass_through_real_latch_shapes(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _get(port, "/api/latch/commitments")
    assert status == 200
    assert body["ok"] is True
    assert body["demo"] is False
    first = body["items"][0]
    # Nested economics/temporal/confidence objects arrive verbatim; HAVEN
    # must not flatten or recompute them.
    assert first["economics"] == {"amount_minor": 1549, "currency": "USD", "cadence": "monthly"}
    assert first["temporal"]["renewal_at"] == "2026-11-02T00:00:00-07:00"
    assert first["temporal"]["cancel_before"] == "2026-10-28T00:00:00-07:00"
    assert first["confidence"] == {"overall": "SUPPORTED"}
    assert len(body["items"]) == 2


def test_demo_label_comes_from_the_service(server, latch, monkeypatch):
    _instance, port = server
    service, _transport = latch
    monkeypatch.setattr(service, "_demo", True)
    status, body = _get(port, "/api/latch/commitments")
    assert status == 200
    assert body["demo"] is True
    assert body["label"] == "Synthetic demo"


def test_access_stale_only_returns_stale_grants_with_stale_reasons(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _get(port, "/api/latch/access?stale_only=1")
    assert status == 200
    assert body["ok"] is True
    assert [item["id"] for item in body["items"]] == ["acc_1", "acc_2"]
    assert all(item["stale"] is True for item in body["items"])
    assert "stale_reason" in body["items"][0]
    assert "454 days" in body["items"][0]["stale_reason"]
    # The stale-only filter was asked for in Latch's own query language.
    access_query = [u for _m, u, _b in transport.requests if urlsplit(u).path == "/v1/access"]
    assert len(access_query) == 1
    assert "stale_only" in access_query[0]


def test_access_without_flag_returns_all_grants(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _get(port, "/api/latch/access")
    assert status == 200
    assert [item["id"] for item in body["items"]] == ["acc_1", "acc_2", "acc_3"]
    access_query = [u for _m, u, _b in transport.requests if urlsplit(u).path == "/v1/access"]
    assert "stale_only" not in access_query[0]


def test_schedules_proposals_returns_scheduler_records(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _get(port, "/api/latch/schedules/proposals")
    assert status == 200
    assert body["ok"] is True
    proposals = body["items"]
    assert [p["id"] for p in proposals] == ["rev_1", "rev_2"]
    # Nested targets and evidence arrays stay objects, not flattened fields.
    assert proposals[0]["target"] == {"access_grant_id": "acc_1"}
    assert proposals[1]["target"] == {"commitment_id": "com_cloud_atlas"}
    assert proposals[0]["evidence"] == [{"evidence_id": "ev_1", "kind": "staleness"}]
    assert transport.paths() == ["/v1/schedules/proposals"]


def test_actions_returns_action_plan_records(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _get(port, "/api/latch/actions")
    assert status == 200
    assert body["ok"] is True
    assert [p["plan_id"] for p in body["items"]] == ["act_9"]
    assert body["items"][0]["status"] == "proposed"
    assert transport.paths() == ["/v1/actions"]

    # Optional filters pass through as fixed query parameters.
    transport.requests.clear()
    status, body = _get(port, "/api/latch/actions?commitment_id=com_streaming_north&status=proposed")
    assert status == 200
    url = transport.requests[0][1]
    assert "commitment_id=com_streaming_north" in url
    assert "status=proposed" in url


def test_proposals_legacy_alias_still_combines_both_records(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _get(port, "/api/latch/proposals")
    assert status == 200
    assert body["ok"] is True
    assert [p["id"] for p in body["scheduler_proposals"]] == ["rev_1", "rev_2"]
    assert [p["plan_id"] for p in body["action_plans"]] == ["act_9"]


def test_evidence_detail_returns_record_fields_at_top_level(server, latch):
    _instance, port = server
    _service, transport = latch
    # Metadata read (no content flag): the page only needs resp.id here.
    status, body = _get(port, "/api/latch/evidence/ev_1")
    assert status == 200
    assert body["ok"] is True
    assert body["id"] == "ev_1"
    assert body["content_hash"] == "sha256:abc123"
    metadata_url = transport.requests[-1][1]
    assert "content=1" not in metadata_url

    # Detail read asks latchd for the blob verbatim.
    status, body = _get(port, "/api/latch/evidence/ev_1?content=1")
    assert status == 200
    assert body["id"] == "ev_1"
    assert body["content"] == "North Streaming subscription renews 2026-11-02."
    content_url = transport.requests[-1][1]
    assert "content=1" in content_url


def test_evidence_legacy_id_alias_and_latch_404(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _get(port, "/api/latch/evidence?id=ev_1")
    assert status == 200
    assert body["id"] == "ev_1"
    status, body = _get(port, "/api/latch/evidence")
    assert status == 400
    assert body["ok"] is False
    status, body = _get(port, "/api/latch/evidence/ev_missing")
    assert status == 404
    assert body["ok"] is False


def test_config_returns_flat_fields_the_page_reads(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _get(port, "/api/latch/config")
    assert status == 200
    # Flat contract: the page reads config.base_url / config.demo directly.
    assert body["ok"] is True
    assert body["base_url"] == "http://127.0.0.1:8787"
    assert body["demo"] is False
    assert body["label"] is None
    assert body["available"] is True
    assert "status" not in body


def test_status_is_a_legacy_alias_of_config(server, latch):
    _instance, port = server
    _service, _transport = latch
    _status, config = _get(port, "/api/latch/config")
    status, body = _get(port, "/api/latch/status")
    assert status == 200
    assert body == config


def test_commitment_detail_returns_record_fields_at_top_level(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _get(port, "/api/latch/commitments/com_streaming_north")
    assert status == 200
    assert body["ok"] is True
    # The UI re-renders from resp.id / resp.economics / ... directly.
    assert body["id"] == "com_streaming_north"
    assert body["economics"]["amount_minor"] == 1549
    assert body["temporal"]["renewal_at"] == "2026-11-02T00:00:00-07:00"
    assert body["confidence"] == {"overall": "SUPPORTED"}
    assert transport.paths() == ["/v1/commitments/com_streaming_north"]


def test_commitment_detail_escapes_the_id(server, latch):
    _instance, port = server
    _service, transport = latch
    transport.routes[("GET", "/v1/commitments/a%20b%2Fc")] = (
        200,
        {},
        {"id": "a b/c", "kind": "subscription"},
    )
    status, body = _get(port, "/api/latch/commitments/a%20b%2Fc")
    assert status == 200
    assert body["id"] == "a b/c"
    # The raw record id never travels as a raw path segment.
    assert "/v1/commitments/a b/c" not in transport.paths()


# --- the one mutation ---------------------------------------------------------


def test_propose_uses_the_stable_idempotency_key(server, latch):
    _instance, port = server
    _service, transport = latch
    status, body = _post(port, "/api/latch/actions/plan", {"commitment_id": "com_streaming_north"})
    assert status == 200
    assert body["ok"] is True
    assert body["plan"]["status"] == "proposed"
    assert body["plan"]["plan_id"] == "act_com_streaming_north"

    # A double-submit/double-click collapses into the same durable plan id.
    status, again = _post(port, "/api/latch/actions/plan", {"commitment_id": "com_streaming_north"})
    assert status == 200
    assert again["plan"]["plan_id"] == body["plan"]["plan_id"]

    posts = [b for m, _u, b in transport.requests if m == "POST"]
    assert len(posts) == 2
    keys = {json.loads(raw)["idempotency_key"] for raw in posts}
    assert keys == {"haven:cancel_subscription:com_streaming_north"}
    # Only Latch's proposal endpoint is ever POSTed to.
    assert set(transport.paths()) == {"/v1/actions/plan"}


def test_propose_legacy_alias_matches_the_canonical_route(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _post(port, "/api/latch/proposals", {"commitment_id": "com_streaming_north"})
    assert status == 200
    assert body["ok"] is True
    assert body["plan"]["plan_id"] == "act_com_streaming_north"


def test_propose_requires_commitment_id(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _post(port, "/api/latch/actions/plan", {})
    assert status == 400
    assert body["ok"] is False


# --- disconnected / reconnect / error honesty ---------------------------------


def test_disconnected_is_honest_and_recovers_per_request(server, latch):
    _instance, port = server
    _service, transport = latch
    transport.fail_next = 1  # the daemon drops exactly one connection
    status, body = _get(port, "/api/latch/commitments")
    assert status == 503
    assert body["ok"] is False
    assert body["error"] == "latch_unreachable"
    # The very next request reconnects: nothing is cached as "down".
    status, body = _get(port, "/api/latch/commitments")
    assert status == 200
    assert body["ok"] is True
    status, body = _get(port, "/api/latch/status")
    assert body["available"] is True


def test_latch_http_error_maps_to_502_with_status(server, latch):
    _instance, port = server
    _service, transport = latch
    transport.routes[("GET", "/v1/commitments")] = (
        500,
        {"Content-Type": "application/json"},
        json.dumps({"error": {"code": "INTERNAL", "message": "db locked"}}).encode(),
    )
    status, body = _get(port, "/api/latch/commitments")
    assert status == 502
    assert body["ok"] is False
    assert body["error"] == "latch_http_error"
    assert body["status"] == 500


def test_non_json_latch_body_maps_to_invalid_response(server, latch):
    _instance, port = server
    _service, transport = latch
    transport.routes[("GET", "/v1/commitments")] = (200, {"Content-Type": "text/html"}, b"<html>nope</html>")
    status, body = _get(port, "/api/latch/commitments")
    assert status == 502
    assert body["ok"] is False
    assert body["error"] == "latch_invalid_response"


# --- handoff honesty ----------------------------------------------------------


def test_handoff_probe_without_plan_id_never_400s(server, latch):
    _instance, port = server
    _service, transport = latch
    # The page probes availability with no plan_id; that must answer the
    # probe, not an error.
    status, body = _get(port, "/api/latch/handoff")
    assert status == 200
    assert body["ok"] is True
    assert body["available"] is False
    assert body["url"] is None


def test_handoff_url_only_when_latch_serves_its_html_ui(server, latch):
    _instance, port = server
    _service, transport = latch
    # API-only daemon: root 404s.
    status, body = _get(port, "/api/latch/handoff?plan_id=act_9")
    assert status == 200
    assert body["ok"] is True
    assert body["available"] is False
    assert body["url"] is None

    # Root answers JSON (the API, not the UI): still no handoff.
    transport.routes[("GET", "/")] = (
        200,
        {"Content-Type": "application/json"},
        json.dumps({"error": "no such route"}).encode(),
    )
    status, body = _get(port, "/api/latch/handoff?plan_id=act_9")
    assert body["available"] is False
    assert body["url"] is None

    # Root serves real HTML: the deep link is exactly root + ?plan_id=.
    transport.routes[("GET", "/")] = (200, {"Content-Type": "text/html; charset=utf-8"}, LATCH_HTML_UI)
    status, body = _get(port, "/api/latch/handoff?plan_id=act_9")
    assert body["available"] is True
    assert body["url"] == "http://127.0.0.1:8787/?plan_id=act_9"

    # And the bare probe now reports availability with a null url.
    status, body = _get(port, "/api/latch/handoff")
    assert body["available"] is True
    assert body["url"] is None


# --- shared appearance settings ------------------------------------------------


def _seed_settings(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_appearance_roundtrip_preserves_native_fields(server, latch):
    instance, port = server
    service, _transport = latch
    settings = service.settings_path
    _seed_settings(
        settings,
        {"mode": "light", "theme": "ember", "density": "compact", "motion": "reduced"},
    )
    status, body = _get(port, "/api/latch/appearance")
    assert status == 200
    # Top-level mode is what the WebUI reads (resp.mode); the full record is
    # nested alongside it.
    assert body["mode"] == "light"
    assert body["appearance"] == {
        "mode": "light",
        "theme": "ember",
        "density": "compact",
        "motion": "reduced",
    }

    # PUT changes only mode; the native ThemeService fields survive untouched.
    status, body = _put(port, "/api/latch/appearance", {"mode": "dark"})
    assert status == 200
    assert body["ok"] is True
    assert body["mode"] == "dark"
    assert body["appearance"]["theme"] == "ember"
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved == {"mode": "dark", "theme": "ember", "density": "compact", "motion": "reduced"}

    # POST is accepted too (form-style clients) with identical semantics.
    status, body = _post(port, "/api/latch/appearance", {"mode": "system"})
    assert status == 200
    assert json.loads(settings.read_text(encoding="utf-8"))["mode"] == "system"
    assert json.loads(settings.read_text(encoding="utf-8"))["theme"] == "ember"


def test_appearance_defaults_without_settings_file(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _get(port, "/api/latch/appearance")
    assert status == 200
    # Native ThemeService defaults, fail-closed.
    assert body["mode"] == "system"
    assert body["appearance"] == {
        "mode": "system",
        "theme": "haven",
        "density": "comfortable",
        "motion": "system",
    }


def test_appearance_fails_closed_on_corrupt_settings(server, latch):
    _instance, port = server
    service, _transport = latch
    service.settings_path.parent.mkdir(parents=True, exist_ok=True)
    service.settings_path.write_text("{not json", encoding="utf-8")
    status, body = _get(port, "/api/latch/appearance")
    assert status == 200
    assert body["mode"] == "system"


def test_appearance_write_rejects_bad_mode_and_extra_keys(server, latch):
    _instance, port = server
    _service, _transport = latch
    status, body = _put(port, "/api/latch/appearance", {"mode": "chartreuse"})
    assert status == 400
    assert body["ok"] is False
    status, body = _put(port, "/api/latch/appearance", {"mode": "dark", "theme": "mono"})
    assert status == 400
    status, body = _put(port, "/api/latch/appearance", {})
    assert status == 400


def test_settings_path_env_override_is_used(tmp_path, monkeypatch):
    settings = tmp_path / "Haven.Desktop" / "settings.json"
    monkeypatch.setenv("HAVEN_LATCH_SETTINGS_PATH", str(settings))
    service = LatchPluginService()
    assert service.settings_path == settings
    service.write_appearance_mode("dark")
    assert json.loads(settings.read_text(encoding="utf-8"))["mode"] == "dark"


def test_settings_write_is_atomic(server, latch):
    _instance, port = server
    service, _transport = latch
    _put(port, "/api/latch/appearance", {"mode": "dark"})
    # No temp litter remains next to the real file.
    leftovers = [p for p in service.settings_path.parent.iterdir() if p.name != "settings.json"]
    assert leftovers == []


# --- structural boundary --------------------------------------------------------


def test_no_authorize_execute_verify_anywhere_on_the_surface():
    service_source = (ROOT / "haven" / "integrations" / "latch" / "service.py").read_text(encoding="utf-8").lower()
    for token in ("authorize", "execute", "verify"):
        assert token not in service_source

    for name in dir(LatchPluginService):
        assert name.lower() not in {
            "authorize",
            "authorize_action",
            "execute",
            "execute_action",
            "verify",
            "verify_action",
        }, f"LatchPluginService must not expose {name}"


def test_web_server_never_proxies_arbitrary_latch_paths(server, latch):
    _instance, port = server
    _service, transport = latch
    # Drive every read route plus the proposal; every request the daemon saw
    # must be one of the fixed adapter paths -- never a caller-supplied URL.
    for path in (
        "/api/latch/commitments",
        "/api/latch/commitments/com_streaming_north",
        "/api/latch/access?stale_only=1",
        "/api/latch/schedules/proposals",
        "/api/latch/actions",
        "/api/latch/evidence/ev_1?content=1",
        "/api/latch/config",
        "/api/latch/handoff",
        "/api/latch/handoff?plan_id=act_9",
    ):
        _get(port, path)
    _post(port, "/api/latch/actions/plan", {"commitment_id": "com_streaming_north"})
    server_source_paths = set(transport.paths())
    assert server_source_paths <= {
        "/v1/commitments",
        "/v1/commitments/com_streaming_north",
        "/v1/access",
        "/v1/schedules/proposals",
        "/v1/actions",
        "/v1/evidence/ev_1",
        "/v1/actions/plan",
        "/",
    }
    # A caller cannot turn a latch route into a proxy of an arbitrary URL.
    status, body = _get(port, "/api/latch/evidence/http:%2f%2fevil.example%2f")
    assert status in {400, 404, 502}
    assert body["ok"] is False
