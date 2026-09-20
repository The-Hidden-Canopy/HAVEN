"""End-to-end: filesystem action authorization + consequence verification
reachable over real HTTP, and pending confirmations surviving an unrelated
`rebuild_director()` triggered by another setup change."""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

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


def test_a_safe_action_executes_over_http_and_is_findable_afterward():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = str(Path(tmp) / "data")

        with _boot(data_dir) as (server, port):
            _post(port, "/api/setup/computer/roots", {"path": str(allowed)})
            _post(port, "/api/setup/computer", {"enabled": True, "read_only": False})
            # Enabling computer access alone takes a fresh install out of
            # demo mode into a real, ownerless household -- an owner must be
            # declared before any authorized action (filesystem or device)
            # can run, the same gate `device_command` already enforces.
            _post(port, "/api/setup/household/people", {"name": "Gerron Smith", "role": "owner"})

            new_folder = str(allowed / "Project Files")
            status, body = _post(
                port,
                "/api/computer/actions",
                {
                    "action": "filesystem.create_folder",
                    "parameters": {"path": new_folder},
                    "justification": "user requested via System > Files",
                },
            )
            assert status == 200
            assert body["ok"] is True
            assert body["success"] is True
            assert Path(new_folder).is_dir()

            status, body = _get_json(port, f"/api/search?q={quote('Project Files')}")
            assert status == 200
            assert any(hit["resource_id"] for hit in body["hits"])

            status, body = _get_json(port, "/api/computer/actions/history")
            assert status == 200
            assert body["ok"] is True
            assert len(body["entries"]) == 1


def test_a_move_requires_confirmation_over_http():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        source = allowed / "notes.txt"
        source.write_text("hello")
        data_dir = str(Path(tmp) / "data")

        with _boot(data_dir) as (server, port):
            _post(port, "/api/setup/computer/roots", {"path": str(allowed)})
            _post(port, "/api/setup/computer", {"enabled": True, "read_only": False})
            _post(port, "/api/setup/household/people", {"name": "Gerron Smith", "role": "owner"})
            _post(port, "/api/setup/computer/scan", {})

            status, search_body = _get_json(port, "/api/search?q=notes")
            resource_id = search_body["hits"][0]["resource_id"]

            destination = str(allowed / "moved.txt")
            status, body = _post(
                port,
                "/api/computer/actions",
                {
                    "action": "filesystem.move",
                    "resource_id": resource_id,
                    "parameters": {"source": str(source), "destination": destination},
                    "justification": "user requested via System > Files",
                },
            )
            assert status == 200
            assert body["status"] == "confirmation_required"
            request_id = body["request_id"]
            assert source.exists()

            # An unrelated setup change (a fresh household declaration)
            # rebuilds the director in place -- the pending confirmation
            # must survive it.
            _post(port, "/api/setup/household/people", {"name": "Riley"})

            status, body = _post(port, "/api/computer/actions/confirm", {"request_id": request_id})
            assert status == 200
            assert body["ok"] is True
            assert body["success"] is True
            assert Path(destination).exists()
            assert not source.exists()
