"""The native bridge is narrow: it returns a folder, setup persists it."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
from pathlib import Path

from haven.web.server import make_server


def _post(port: int, path: str) -> tuple[int, dict]:
    for attempt in range(3):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = json.loads(response.read())
            return response.status, body
        except ConnectionAbortedError:
            if attempt == 2:
                raise
            time.sleep(0.05)
        finally:
            connection.close()


def _get(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read())
    status = response.status
    connection.close()
    return status, body


def test_native_folder_picker_bridge_returns_selected_path():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _ = make_server(
            0,
            data_dir=Path(tmp) / "data",
            demo=True,
            folder_picker=lambda: "C:/Users/Gerron/Documents",
        )
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            port = instance.server_address[1]
            status, body = _get(port, "/api/host/capabilities")
            assert status == 200
            assert body == {"host": "desktop", "capabilities": ["folder_picker"]}

            status, body = _post(port, "/api/host/pick-folder")
            assert status == 200
            assert body == {"ok": True, "path": "C:/Users/Gerron/Documents"}
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)


def test_legacy_desktop_folder_picker_alias_remains_compatible():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _ = make_server(
            0,
            data_dir=Path(tmp) / "data",
            demo=True,
            folder_picker=lambda: "C:/Users/Gerron/Documents",
        )
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = _post(instance.server_address[1], "/api/desktop/pick-folder")
            assert status == 200
            assert body == {"ok": True, "path": "C:/Users/Gerron/Documents"}
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)


def test_browser_mode_reports_native_picker_unavailable_without_fabricating_a_path():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _ = make_server(0, data_dir=Path(tmp) / "data", demo=True)
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            port = instance.server_address[1]
            status, body = _get(port, "/api/host/capabilities")
            assert status == 200
            assert body == {"host": "browser", "capabilities": []}

            status, body = _post(port, "/api/host/pick-folder")
            assert status == 200
            assert body["ok"] is False
            assert "Desktop" in body["error"]
            assert "path" not in body
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)


def test_native_folder_picker_can_feed_the_existing_data_dir_setup_path():
    with tempfile.TemporaryDirectory() as tmp:
        chosen = Path(tmp) / "chosen"
        chosen.mkdir()
        instance, _ = make_server(
            0,
            data_dir=Path(tmp) / "data",
            demo=True,
            folder_picker=lambda: str(chosen),
        )
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            port = instance.server_address[1]
            status, picked = _post(port, "/api/host/pick-folder")
            assert status == 200
            assert picked == {"ok": True, "path": str(chosen)}

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            body = json.dumps({"path": picked["path"]}).encode("utf-8")
            connection.request(
                "POST",
                "/api/setup/data-dir",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            result = json.loads(response.read())
            connection.close()
            assert response.status == 200
            assert result["ok"] is True
            assert Path(result["setup"]["data_dir"]["resolved"]) == chosen.resolve()
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)
