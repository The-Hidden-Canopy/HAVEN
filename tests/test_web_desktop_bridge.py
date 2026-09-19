"""The native bridge is narrow: it returns a folder, setup persists it."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from pathlib import Path

from haven.web.server import make_server


def _post(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
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
            status, body = _post(instance.server_address[1], "/api/desktop/pick-folder")
            assert status == 200
            assert body["ok"] is False
            assert "Desktop" in body["error"]
            assert "path" not in body
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)
