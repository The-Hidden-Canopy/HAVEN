"""End-to-end: enabling the computer provider over real HTTP takes a fresh
install out of demo mode, and a real scan is findable through search."""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)


@contextmanager
def _boot(data_dir: str):
    from haven.web.server import make_server

    instance, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload)
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def test_enabling_computer_access_takes_a_fresh_install_out_of_demo_mode():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = str(Path(tmp) / "data")

        with _boot(data_dir) as (server, port):
            assert server.director.house is not None  # fresh boot: demo

            status, body = _post(port, "/api/setup/computer/roots", {"path": str(allowed)})
            assert status == 200 and body["ok"] is True

            status, body = _post(port, "/api/setup/computer", {"enabled": True})
            assert status == 200 and body["ok"] is True

            # The live director was rebuilt in place -- no restart needed,
            # the same swap connecting Home Assistant or a community
            # provider already does.
            assert server.director.house is None


def test_scan_over_http_populates_resources_findable_through_search():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        (allowed / "nasa_report.txt").write_text("mission details")
        data_dir = str(Path(tmp) / "data")

        with _boot(data_dir) as (server, port):
            _post(port, "/api/setup/computer/roots", {"path": str(allowed)})
            _post(port, "/api/setup/computer", {"enabled": True})

            status, body = _post(port, "/api/setup/computer/scan", {})
            assert status == 200
            assert body["ok"] is True
            assert body["scanned"] >= 2

            status, body = _get_json(port, "/api/search?q=nasa")
            assert status == 200
            assert body["ok"] is True
            assert any(hit["resource_id"].endswith("nasa_report.txt") for hit in body["hits"])


def test_http_setup_keeps_computer_read_only_until_write_is_explicitly_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = str(Path(tmp) / "data")

        with _boot(data_dir) as (_server, port):
            _post(port, "/api/setup/computer/roots", {"path": str(allowed)})

            status, body = _post(port, "/api/setup/computer", {"enabled": True})
            assert status == 200
            assert body["setup"]["computer"]["read_only"] is True

            status, body = _post(
                port, "/api/setup/computer", {"enabled": True, "read_only": False}
            )
            assert status == 200
            assert body["setup"]["computer"]["read_only"] is False
