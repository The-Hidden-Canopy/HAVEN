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
from haven.web.diagnostics import BackupManager
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore

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
            # This is descriptive metadata, not credential material. The
            # export may safely say which named scopes a provider requires,
            # but it must never include a secret or credential reference.
            if lowered == "required_credential_scopes":
                _assert_no_secret_looking_keys(value)
                continue
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


def test_configured_home_assistant_export_keeps_operational_metadata_secret_free() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        secret = "home-assistant-test-secret"
        SetupConfigStore(data_dir / "haven.json").save(
            SetupConfig(
                completed=True,
                data_dir=str(data_dir),
                provider_kind="home_assistant",
                provider_base_url="http://ha.local:8123",
                provider_token_file="ha_token.txt",
            )
        )
        (data_dir / "ha_token.txt").write_text(secret, encoding="utf-8")
        with _boot(data_dir) as (_instance, _director, port):
            status, body = _get_json(port, "/api/system/diagnostics/export")
            assert status == 200
            export = body["export"]
            provider = export["diagnostics"]["provider"]
            assert provider["operational"]["mutation"] is True
            assert provider["operational"]["required_credential_scopes"] == []
            assert secret not in json.dumps(export, sort_keys=True)
            _assert_no_secret_looking_keys(export)
            backup = BackupManager(data_dir=data_dir).create()
            backup_dir = data_dir / "backups" / backup["id"]
            assert all(secret.encode("utf-8") not in path.read_bytes() for path in backup_dir.iterdir() if path.is_file())


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
