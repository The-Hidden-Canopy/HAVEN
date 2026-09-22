"""HTTP vertical slice for the plugin marketplace: GET/refresh/enable/disable.

Boots a real HavenWebServer (mirroring tests/test_web_knowledge.py) and
monkeypatches the catalog fetch so the test never touches the network --
the fetch/verify contract itself is covered by test_plugins_catalog_client.py.
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.plugins.catalog_client import CatalogFetchError, CatalogSnapshot
from haven.plugins.contracts import PluginCapability, PluginDataBoundary, PluginDescriptor, PluginStatus
import haven.web.server as server_module

NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)

TRACEGLASS = PluginDescriptor(
    plugin_id="traceglass",
    display_name="TraceGlass",
    publisher="The Hidden Canopy LLC",
    capability=PluginCapability.DECISION_CHAIN_RECONSTRUCTION,
    status=PluginStatus.CATALOG_ONLY,
    data_boundary=PluginDataBoundary.EXPORTED_RECEIPTS_ONLY,
    description="Reconstructs decision chains from exported receipts.",
)
GHOST_TEACHER = PluginDescriptor(
    plugin_id="ghost-teacher",
    display_name="Ghost Teacher",
    publisher="The Hidden Canopy LLC",
    capability=PluginCapability.ADAPTIVE_CURRICULUM_EVALUATION,
    status=PluginStatus.CATALOG_ONLY,
    data_boundary=PluginDataBoundary.EXPORTED_RECEIPTS_ONLY,
    description="Proposes training curriculum from exported evaluation signals.",
)


@pytest.fixture(autouse=True)
def fake_catalog_fetch(monkeypatch):
    import haven.plugins.manager as manager_module

    snapshot = CatalogSnapshot(
        catalog_version="test-1", issued_at=NOW, expires_at=NOW, plugins=(TRACEGLASS, GHOST_TEACHER)
    )
    monkeypatch.setattr(manager_module, "fetch_catalog", lambda url: snapshot)


@contextmanager
def _boot(data_dir: Path):
    instance, _ = server_module.make_server(0, data_dir=str(data_dir), clock=lambda: NOW)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(
        "POST", path, body=json.dumps(payload or {}), headers={"Content-Type": "application/json"}
    )
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def test_plugins_are_empty_until_the_first_refresh(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        status, body = _get(port, "/api/plugins")
        assert status == 200
        assert body["ok"] is True
        assert body["plugins"] == []
        assert body["catalog_error"] is None


def test_refresh_populates_the_catalog_and_a_plain_get_then_sees_it(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        status, body = _post(port, "/api/plugins/refresh")
        assert status == 200
        assert body["ok"] is True
        ids = sorted(p["plugin_id"] for p in body["plugins"])
        assert ids == ["ghost-teacher", "traceglass"]
        assert all(p["enabled"] is False for p in body["plugins"])

        status, body = _get(port, "/api/plugins")
        assert status == 200
        assert sorted(p["plugin_id"] for p in body["plugins"]) == ["ghost-teacher", "traceglass"]


def test_enable_then_disable_round_trips_over_http(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        _post(port, "/api/plugins/refresh")

        status, body = _post(port, "/api/plugins/traceglass/enable")
        assert status == 200
        assert body["ok"] is True
        by_id = {p["plugin_id"]: p["enabled"] for p in body["plugins"]}
        assert by_id == {"traceglass": True, "ghost-teacher": False}

        status, body = _post(port, "/api/plugins/traceglass/disable")
        assert status == 200
        assert all(p["enabled"] is False for p in body["plugins"])


def test_enabling_an_unlisted_plugin_id_fails_cleanly_not_with_a_traceback(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        _post(port, "/api/plugins/refresh")
        status, body = _post(port, "/api/plugins/not-a-real-plugin/enable")
        assert status == 200
        assert body["ok"] is False
        assert "not-a-real-plugin" in body["error"]


def test_enablement_survives_a_server_restart(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        _post(port, "/api/plugins/refresh")
        _post(port, "/api/plugins/traceglass/enable")

    with _boot(tmp_path) as (_instance, port):
        _post(port, "/api/plugins/refresh")
        status, body = _get(port, "/api/plugins")
        by_id = {p["plugin_id"]: p["enabled"] for p in body["plugins"]}
        assert by_id["traceglass"] is True


def test_an_unknown_plugin_action_path_is_a_clean_404(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        status, _body = _post(port, "/api/plugins/traceglass/do-something-else")
        assert status == 404


def test_a_catalog_fetch_failure_is_reported_without_losing_a_previous_good_snapshot(tmp_path, monkeypatch):
    import haven.plugins.manager as manager_module

    with _boot(tmp_path) as (_instance, port):
        _post(port, "/api/plugins/refresh")

        monkeypatch.setattr(
            manager_module,
            "fetch_catalog",
            lambda url: (_ for _ in ()).throw(CatalogFetchError("unreachable", code="unreachable")),
        )
        status, body = _post(port, "/api/plugins/refresh")
        assert status == 200
        assert body["ok"] is True
        assert body["catalog_error"] == "unreachable"
        assert sorted(p["plugin_id"] for p in body["plugins"]) == ["ghost-teacher", "traceglass"]
