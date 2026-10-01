"""HTTP compatibility surface for durable resource automations."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from haven.web.server import make_server

STATIC_ROOT = Path(__file__).parents[1] / "haven" / "web" / "static"

NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)


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


def _spec(household_id: str) -> dict:
    return {
        "spec_id": "spec-http-1",
        "household_id": household_id,
        "trigger": {
            "kind": "event",
            "parameters": [["event_name", "task.status_changed"], ["to_state", "done"]],
        },
        "selector": {"parameters": []},
        "action": {
            "domain": "computer",
            "action": "computer.noop",
            "consequence_class": "reversible_local",
            "parameters": [["resource_id", "file-1"]],
        },
        "source_text": "when the task is done, perform the local action",
        "created_by": "owner-1",
    }


def test_resource_automation_http_lifecycle_round_trip() -> None:
    with _boot() as (port, _server, director):
        status, body = _request(
            port,
            "POST",
            "/api/resource-automations",
            {"rule_id": "http-rule-1", "spec": _spec(director.household_id)},
        )
        assert status == 200
        assert body["ok"] is True
        assert body["automation"]["status"] == "proposed"

        status, body = _request(
            port,
            "POST",
            "/api/resource-automations/http-rule-1/approve",
            {"justification": "approve the governed resource rule"},
        )
        assert status == 200
        assert body["automation"]["status"] == "approved"

        status, body = _request(port, "GET", "/api/resource-automations")
        assert status == 200
        assert body["ok"] is True
        assert body["automations"][0]["rule_id"] == "http-rule-1"

        status, body = _request(
            port,
            "POST",
            "/api/resource-automations/http-rule-1/enable",
            {"enabled": False, "justification": "pause while the provider is unavailable"},
        )
        assert status == 200
        assert body["automation"]["enabled"] is False


def test_resource_automation_http_rejects_foreign_household() -> None:
    with _boot() as (port, _server, director):
        status, body = _request(
            port,
            "POST",
            "/api/resource-automations",
            {"rule_id": "foreign", "spec": _spec(f"{director.household_id}-foreign")},
        )
        assert status == 400
        assert body["ok"] is False


def test_web_panel_projects_resource_automation_lifecycle() -> None:
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    for element_id in (
        "resource-automations-list",
        "resource-automations-count",
        "resource-automations-refresh",
        "resource-automations-error",
    ):
        assert f'id="{element_id}"' in index
    for route in (
        "/api/resource-automations",
        "'/api/resource-automations/' + encodeURIComponent(ruleId) + '/approve'",
        "'/api/resource-automations/' + encodeURIComponent(ruleId) + '/enable'",
        "'/api/resource-automations/' + encodeURIComponent(ruleId) + '/revoke'",
    ):
        assert route in app
    for marker in ("renderResourceAutomations", "resource_automations.changed", "resource-automation-row"):
        assert marker in app or marker in styles
