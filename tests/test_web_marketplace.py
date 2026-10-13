"""HTTP discovery surface for the read-only Marketplace slice."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from haven.ipc import request_message
from haven.web import server as server_module


@contextmanager
def _boot(data_dir: Path):
    instance, _ = server_module.make_server(0, data_dir=str(data_dir))
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get(port: int, path: str):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read())
    status = response.status
    connection.close()
    return status, body


def _post(port: int, path: str):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, body="{}", headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    body = json.loads(response.read())
    status = response.status
    connection.close()
    return status, body


def test_marketplace_is_available_without_network_or_settings_state(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        status, body = _get(port, "/api/marketplace?release_state=coming_soon")
        assert status == 200
        assert body["ok"] is True
        assert body["source"] == "bundled_preview"
        assert body["signature_verified"] is False
        assert body["install_available"] is False
        assert body["items"]
        assert all(item["release_state"] == "coming_soon" for item in body["items"])
        assert all("install_command" not in item for item in body["items"])


def test_marketplace_refresh_fails_closed_and_keeps_the_local_preview(tmp_path):
    with _boot(tmp_path) as (_instance, port):
        status, body = _post(port, "/api/marketplace/refresh")
        assert status == 200
        assert body["ok"] is True
        assert body["catalog_error"] == "not_configured"
        assert body["items"]
        assert body["install_available"] is False


def test_marketplace_ipc_preserves_discovery_only_boundary(tmp_path):
    with _boot(tmp_path) as (instance, _port):
        response = instance.build_ipc_dispatcher()(
            request_message(
                "marketplace-test",
                "marketplace.list",
                {"release_state": "coming_soon"},
            )
        )
        assert response["ok"] is True
        result = response["result"]
        assert result["items"]
        assert all(item["release_state"] == "coming_soon" for item in result["items"])
        assert result["install_available"] is False
