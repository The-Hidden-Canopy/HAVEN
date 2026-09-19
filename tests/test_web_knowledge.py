"""HTTP vertical slice for durable, user-visible knowledge."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)


@contextmanager
def _boot(data_dir: Path):
    from haven.web.server import make_server

    instance, _ = make_server(0, data_dir=str(data_dir), clock=lambda: NOW)
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
    connection.request(
        "POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"}
    )
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def _get(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def _enable_and_scan(port: int, allowed: Path) -> None:
    assert _post(port, "/api/setup/computer/roots", {"path": str(allowed)})[1]["ok"]
    assert _post(port, "/api/setup/computer", {"enabled": True})[1]["ok"]
    assert _post(
        port,
        "/api/setup/household/people",
        {"name": "Gerron", "role": "owner"},
    )[1]["ok"]
    assert _post(port, "/api/setup/computer/scan", {})[1]["ok"]


def test_file_scan_claim_search_and_restart_survive():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Proposals"
        root.mkdir()
        (root / "proposal.md").write_text(
            "The proposal concerns lunar site preparation.\n", encoding="utf-8"
        )
        data_dir = Path(tmp) / "data"

        with _boot(data_dir) as (_, port):
            _enable_and_scan(port, root)
            status, body = _get(port, "/api/knowledge/claims")
            assert status == 200 and body["ok"] is True
            assert len(body["claims"]) == 1
            claim_id = body["claims"][0]["claim_id"]
            assert body["claims"][0]["state"] == "reported"
            assert body["claims"][0]["provenance"] == "document_stated"

            status, search = _get(port, "/api/search?q=" + quote("lunar site preparation"))
            assert status == 200
            assert any(hit["matched_refs"] == [claim_id] for hit in search["hits"])

        with _boot(data_dir) as (_, port):
            status, body = _get(port, "/api/knowledge/claims")
            assert status == 200
            assert [claim["claim_id"] for claim in body["claims"]] == [claim_id]


def test_changed_file_stales_old_claim_and_forget_does_not_resurrect_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Documents"
        root.mkdir()
        document = root / "notes.txt"
        document.write_text("The launch date is October 1.\n", encoding="utf-8")
        data_dir = Path(tmp) / "data"

        with _boot(data_dir) as (_, port):
            _enable_and_scan(port, root)
            claims = _get(port, "/api/knowledge/claims")[1]["claims"]
            old_id = claims[0]["claim_id"]
            document.write_text("The launch date is October 2.\n", encoding="utf-8")
            assert _post(port, "/api/setup/computer/scan", {})[1]["ok"]

            current = _get(port, "/api/knowledge/claims")[1]["claims"]
            assert len(current) == 1
            all_claims = _get(port, "/api/knowledge/claims?include_stale=true")[1]["claims"]
            assert len(all_claims) == 2
            assert any(claim["claim_id"] == old_id and claim["state"] == "stale" for claim in all_claims)

            # Explicit forget is owner-bound and leaves a tombstone rather
            # than deleting the old row.
            new_id = next(claim["claim_id"] for claim in current if claim["claim_id"] != old_id)
            status, forgotten = _post(
                port,
                f"/api/knowledge/claims/{quote(new_id, safe='')}/forget",
                {},
            )
            assert status == 200 and forgotten["ok"] is True
            assert _get(port, "/api/knowledge/claims")[1]["claims"] == []

            document.write_text("The launch date is October 2.\n", encoding="utf-8")
            assert _post(port, "/api/setup/computer/scan", {})[1]["ok"]
            assert _get(port, "/api/knowledge/claims")[1]["claims"] == []


def test_knowledge_changes_require_a_declared_owner():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Documents"
        root.mkdir()
        (root / "notes.txt").write_text("A source statement.\n", encoding="utf-8")
        data_dir = Path(tmp) / "data"

        with _boot(data_dir) as (_, port):
            assert _post(port, "/api/setup/computer/roots", {"path": str(root)})[1]["ok"]
            assert _post(port, "/api/setup/computer", {"enabled": True})[1]["ok"]
            assert _post(port, "/api/setup/computer/scan", {})[1]["ok"]
            claim_id = _get(port, "/api/knowledge/claims")[1]["claims"][0]["claim_id"]
            status, body = _post(
                port,
                f"/api/knowledge/claims/{claim_id}/correct",
                {"proposition": "A corrected statement."},
            )
            assert status == 400
            assert "owner" in body["error"]


def test_knowledge_correction_uses_declared_owner_not_first_member():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Documents"
        root.mkdir()
        (root / "notes.txt").write_text("A source statement.\n", encoding="utf-8")
        data_dir = Path(tmp) / "data"

        with _boot(data_dir) as (_, port):
            assert _post(port, "/api/setup/computer/roots", {"path": str(root)})[1]["ok"]
            assert _post(port, "/api/setup/computer", {"enabled": True})[1]["ok"]
            # The first declared person is only a member. The owner arrives
            # later, so this catches accidental use of `director.resident`.
            assert _post(
                port,
                "/api/setup/household/people",
                {"name": "Bryan", "role": "member"},
            )[1]["ok"]
            assert _post(
                port,
                "/api/setup/household/people",
                {"name": "Gerron", "role": "owner"},
            )[1]["ok"]
            assert _post(port, "/api/setup/computer/scan", {})[1]["ok"]
            claim_id = _get(port, "/api/knowledge/claims")[1]["claims"][0]["claim_id"]

            status, body = _post(
                port,
                f"/api/knowledge/claims/{quote(claim_id, safe='')}/correct",
                {"proposition": "The owner corrected this statement."},
            )
            assert status == 200 and body["ok"] is True
            assert "user:gerron" in body["claim"]["evidence_refs"]
            assert "user:bryan" not in body["claim"]["evidence_refs"]


def test_claims_move_with_data_dir_and_restore_from_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Documents"
        root.mkdir()
        (root / "notes.txt").write_text("A durable project statement.\n", encoding="utf-8")
        old_data = Path(tmp) / "old-data"
        new_data = Path(tmp) / "new-data"

        with _boot(old_data) as (server, port):
            _enable_and_scan(port, root)
            claim_id = _get(port, "/api/knowledge/claims")[1]["claims"][0]["claim_id"]

            status, moved = _post(port, "/api/setup/data-dir", {"path": str(new_data)})
            assert status == 200 and moved["ok"] is True
            assert server.claims.path == new_data / "claims.db"
            assert _get(port, "/api/knowledge/claims")[1]["claims"][0]["claim_id"] == claim_id

            backup = _post(port, "/api/system/backup", {})[1]["backup"]
            assert "claims.db" in backup["files"]
            assert server.claims.mark_stale(claim_id) is True
            restored = _post(port, "/api/system/backup/restore", {"id": backup["id"]})
            assert restored[0] == 200
            assert _get(port, "/api/knowledge/claims")[1]["claims"][0]["claim_id"] == claim_id

        with _boot(new_data) as (_, port):
            assert _get(port, "/api/knowledge/claims")[1]["claims"][0]["claim_id"] == claim_id
