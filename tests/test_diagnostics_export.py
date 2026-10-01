"""Diagnostic export (native product-consolidation plan, P0 "Runtime"): a
support-safe bundle covering version/commit/installation id, provider
state, model assignments, pending confirmations, scheduler state, and
recent receipts -- reachable over both IPC and HTTP, never carrying secrets."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@contextmanager
def _boot(data_dir: Path):
    instance, director = make_server(0, data_dir=str(data_dir), clock=lambda: NOW)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, director, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, body


def _assert_no_secret_looking_keys(payload) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = key.lower()
            assert not any(bad in lowered for bad in ("token", "secret", "password", "credential")), (
                f"diagnostic export must never carry a field named {key!r}"
            )
            _assert_no_secret_looking_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secret_looking_keys(item)


def test_export_shape_and_no_secrets_over_http() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        with _boot(data_dir) as (_instance, _director, port):
            status, body = _get_json(port, "/api/system/diagnostics/export")
            assert status == 200
            assert body["ok"] is True
            export = body["export"]
            assert set(export) == {
                "generated_at",
                "version",
                "commit",
                "installation_id",
                "diagnostics",
                "pending_confirmations",
                "model_assignments",
                "event_channel",
                "recent_receipts",
                "unified_receipts",
            }
            assert isinstance(export["installation_id"], str) and export["installation_id"]
            assert export["pending_confirmations"] == 0
            assert all(value is None for value in export["model_assignments"].values())
            assert export["recent_receipts"] == []
            assert export["unified_receipts"] == []
            _assert_no_secret_looking_keys(export)


def test_export_reachable_over_ipc_and_matches_installation_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        instance, _director = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            dispatcher = instance.build_ipc_dispatcher()
            result = dispatcher(request_message("req", "system.diagnostics.export", {}))["result"]
            assert result["ok"] is True

            from haven.ipc.named_pipe import installation_id_for_data_dir

            assert result["export"]["installation_id"] == installation_id_for_data_dir(data_dir)
        finally:
            instance.server_close()
