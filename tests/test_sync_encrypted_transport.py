"""AES-GCM folder transport: confidentiality, integrity, and restart shape."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from haven.ipc import request_message
from haven.sync import EncryptedFolderSyncTransport
from haven.sync import LocalSyncEngine
from haven.sync.events import SyncEvent
from haven.web.server import make_server


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
KEY = "shared-folder-key"


def _event() -> SyncEvent:
    return SyncEvent(
        event_id="evt-1",
        seq=1,
        origin_device_id="device:a",
        object_id="task:1",
        kind="task",
        scope_id="scope:personal",
        revision=1,
        causal_parents=(),
        payload=(
            ("scope_id", "scope:personal"),
            ("title", "private task title"),
        ),
        occurred_at=NOW,
    ).signed(KEY.encode("utf-8"))


def test_encrypted_folder_round_trip_does_not_write_plaintext(tmp_path: Path) -> None:
    sender = EncryptedFolderSyncTransport(
        export_dir=tmp_path / "out", import_dir=tmp_path / "unused", key=KEY
    )
    receiver = EncryptedFolderSyncTransport(
        export_dir=tmp_path / "receiver", import_dir=tmp_path / "out", key=KEY
    )

    event = _event()
    assert sender.send((event,)) == 1
    encrypted = tmp_path / "out" / "outbox.encrypted.jsonl"
    raw = encrypted.read_text(encoding="ascii")
    assert "private task title" not in raw
    assert "event_id" not in raw

    events, watermark = receiver.fetch(since_seq=0)
    assert events == (event,)
    assert watermark == 1


def test_wrong_key_and_tampering_fail_closed_without_advancing_watermark(tmp_path: Path) -> None:
    sender = EncryptedFolderSyncTransport(
        export_dir=tmp_path / "out", import_dir=tmp_path / "unused", key=KEY
    )
    sender.send((_event(),))
    encrypted = tmp_path / "out" / "outbox.encrypted.jsonl"

    wrong_key = EncryptedFolderSyncTransport(
        export_dir=tmp_path / "wrong", import_dir=tmp_path / "out", key="wrong-key"
    )
    assert wrong_key.fetch(since_seq=0) == ((), 0)

    line = encrypted.read_text(encoding="ascii").strip()
    encrypted.write_text(line[:-1] + ("A" if line[-1] != "A" else "B") + "\n", encoding="ascii")
    assert wrong_key.fetch(since_seq=0) == ((), 0)


def test_encrypted_record_is_not_plain_json(tmp_path: Path) -> None:
    sender = EncryptedFolderSyncTransport(
        export_dir=tmp_path / "out", import_dir=tmp_path / "unused", key=KEY
    )
    sender.send((_event(),))
    line = (tmp_path / "out" / "outbox.encrypted.jsonl").read_text(encoding="ascii").strip()
    try:
        json.loads(line)
    except json.JSONDecodeError:
        pass
    else:  # pragma: no cover - protects the format contract if it regresses
        raise AssertionError("encrypted sync records must not be JSON objects")


def test_engine_rehydrates_encrypted_transport_and_ipc_selects_it(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    engine = LocalSyncEngine(data_dir=data_dir, clock=lambda: NOW)
    engine.set_transport(
        EncryptedFolderSyncTransport(
            export_dir=tmp_path / "out", import_dir=tmp_path / "in", key=KEY
        ),
        auth_key=KEY,
    )
    assert engine.set_enabled(True)["ok"] is True

    reopened = LocalSyncEngine(data_dir=data_dir, clock=lambda: NOW)
    assert reopened.status()["transport"] == "EncryptedFolderSyncTransport"
    assert reopened._transport.kind == "encrypted_folder"

    server, _ = make_server(0, data_dir=tmp_path / "server", clock=lambda: NOW)
    try:
        result = server.build_ipc_dispatcher()(
            request_message(
                "sync-config",
                "sync.transport.set",
                {
                    "export_dir": str(tmp_path / "ipc-out"),
                    "import_dir": str(tmp_path / "ipc-in"),
                    "transport": "encrypted_folder",
                    "auth_key": KEY,
                },
            )
        )["result"]
        assert result == {
            "ok": True,
            "transport": "EncryptedFolderSyncTransport",
            "authenticated": True,
        }
    finally:
        server.server_close()
