"""Stable vocabulary, safe wire failures and durable incident grouping."""
import json
import socket
import pytest
from pathlib import Path

from haven.faults import REGISTRY, SUBSYSTEMS, FaultJournal, describe, public_failure
from haven.ipc import IpcDispatcher, request_message
from haven.web.server import make_server


def test_verification_does_not_contact_real_integrations():
    with pytest.raises(PermissionError, match="loopback"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)
    with pytest.raises(PermissionError, match="loopback"):
        socket.getaddrinfo("synthetic.invalid", 443)


def test_packaged_build_identity_does_not_inherit_an_ancestor_checkout(tmp_path, monkeypatch):
    from haven.web import diagnostics
    package = tmp_path / "package"
    package.mkdir()
    monkeypatch.setattr(diagnostics.subprocess, "run", lambda *a, **k: pytest.fail("Package must not query parent Git repository"))
    assert diagnostics._git_commit(package) is None
    expected = "a" * 40
    (package / "bundle-manifest.json").write_text(json.dumps({"source_commit": expected}))
    assert diagnostics._git_commit(package) == expected
    (package / "bundle-manifest.json").write_text(json.dumps({"source_commit": "private injected value"}))
    assert diagnostics._git_commit(package) is None


def test_every_actual_top_level_subsystem_has_a_code_family():
    roots = {p.parent.name for p in (Path(__file__).parents[1] / "haven").glob("*/__init__.py")}
    assert roots <= set(SUBSYSTEMS)
    assert all(code == row.code and code.startswith("HAVEN.") for code, row in REGISTRY.items())
    assert {"HAVEN.IPC.UNAVAILABLE", "HAVEN.CORE.STARTUP_FAILED", "HAVEN.CONFIG.INVALID", "HAVEN.UNKNOWN.UNEXPECTED"} <= set(REGISTRY)


def test_confirmed_failure_is_separate_from_probable_causes_and_unknown_text_is_private(tmp_path):
    journal = FaultJournal(tmp_path / "journal")
    secret = "private-user-token and C:/PrivateHousehold/record.json"
    dispatcher = IpcDispatcher({"decisions.fail": lambda _: (_ for _ in ()).throw(OSError(secret))}, fault_journal=journal)
    for ref in ["one", "two"]:
        result = dispatcher(request_message(ref, "decisions.fail", {}))
        assert result["fault"]["code"] == "HAVEN.STORAGE.IO_FAILED"
        assert result["fault"]["correlation_id"] == ref
        assert secret not in json.dumps(result)
        assert result["fault"]["probable_causes"][0]["confidence"] == "possible"
    summary = journal.summary()
    assert summary[0]["count"] == 2
    assert summary[0]["first_seen"] <= summary[0]["last_seen"]
    assert "correlations" not in summary[0]
    private = json.loads((tmp_path / "journal/private-evidence.json").read_text())
    assert secret in private[0]["original"]


def test_equivalent_http_and_ipc_failures_share_canonical_codes():
    a = public_failure({"ok": False, "error": "scope_denied"}, "/api/decisions/review", correlation_id="a")
    b = public_failure({"ok": False, "error": "scope_denied"}, "decisions.review", correlation_id="b")
    assert a["fault"]["code"] == b["fault"]["code"] == "HAVEN.AUTHORITY.SCOPE_DENIED"
    assert a["fault"]["operation"] == b["fault"]["operation"]
    unknown = describe("unknown private text", "unrecognized/private/id", exception=RuntimeError())
    assert unknown["code"] == "HAVEN.UNKNOWN.UNEXPECTED"
    assert "unrecognized/private/id" not in json.dumps(unknown)
    assert "unknown private text" not in json.dumps(unknown)


def test_support_export_omits_private_paths_ids_receipt_evidence_and_future_fields(tmp_path, monkeypatch):
    server, _ = make_server(0, data_dir=tmp_path / "data", models_root=tmp_path / "models")
    marker = "PRIVATE-SYNTHETIC-EVIDENCE"
    try:
        base = server.diagnostics.collect()["diagnostics"]
        base.update(data_dir=marker, config_error=marker, future_private_field=marker)
        base["provider"].update(base_url=marker, token=marker)
        base["knowledge"]["by_state"] = {marker: 9}
        monkeypatch.setattr(server.diagnostics, "collect", lambda: {"diagnostics": base})
        monkeypatch.setattr(server.diagnostics, "receipts", lambda: {"ok": True, "unified_receipts": [{"target": marker, "actor": marker, "correlation_id": marker, "execution_success": True}]})
        server.faults.record(describe("scope_denied", "decisions.review", correlation_id=marker), marker)
        body = server.diagnostics.export()
        assert body["export"]["receipt_summary"]["executed"] == 1
        assert body["export"]["incidents"][0]["code"] == "HAVEN.AUTHORITY.SCOPE_DENIED"
        assert marker not in json.dumps(body)
        assert all(key not in body["export"] for key in ("installation_id", "model_assignments", "recent_receipts", "unified_receipts"))
    finally:
        server.server_close()


def test_local_receipts_are_owner_guarded_and_remain_separate(tmp_path):
    server, _ = make_server(0, data_dir=tmp_path / "data", models_root=tmp_path / "models")
    try:
        assert server.diagnostics.receipts() == {"ok": False, "error": "owner_required"}
        assert server.setup.declare_person(name="Synthetic Owner", role="owner")["ok"]
        assert server.diagnostics.receipts() == {"ok": True, "unified_receipts": []}
    finally:
        server.server_close()


def test_forged_fault_and_corrupt_journal_cannot_export_private_evidence(tmp_path):
    marker = "PRIVATE-SYNTHETIC-EVIDENCE"
    forged = public_failure({"ok": False, "error": marker,
                            "fault": {"code": "HAVEN.CONFIG.INVALID", "message": marker, "evidence": marker}}, "config.update")
    assert marker not in json.dumps(forged)
    server, _ = make_server(0, data_dir=tmp_path / "data", models_root=tmp_path / "models")
    try:
        server.faults.root.mkdir()
        (server.faults.root / "incidents.json").write_text(json.dumps({"schema_version": 1, "incidents": [{"code": marker}]}))
        exported = server.diagnostics.export()
        assert exported["ok"] is False
        assert exported["fault"]["code"] == "HAVEN.EXPORT.REDACTION_FAILED"
        assert marker not in json.dumps(exported)
    finally:
        server.server_close()


def test_retained_dispatcher_uses_current_journal_after_installation_move(tmp_path):
    server, _ = make_server(0, data_dir=tmp_path / "first", models_root=tmp_path / "models")
    try:
        dispatcher = server.build_ipc_dispatcher()
        dispatcher(request_message("before", "decisions.context", {"scope_id": "forged-scope"}))
        old = server.faults.root
        moved = server.setup.choose_data_dir(str(tmp_path / "second"))
        assert moved["ok"], moved
        dispatcher(request_message("after", "decisions.context", {"scope_id": "forged-scope"}))
        assert server.faults.root != old
        assert not old.exists()
        assert server.faults.summary()[0]["count"] == 2
    finally:
        server.server_close()


def test_receipt_view_rechecks_current_household_visibility(tmp_path):
    from datetime import datetime, timedelta, timezone
    from haven.scopes.models import Membership
    server, _ = make_server(0, data_dir=tmp_path / "data", models_root=tmp_path / "models")
    try:
        assert server.setup.declare_person(name="Synthetic Owner", role="owner")["ok"]
        assert server.diagnostics.receipts()["ok"]
        now = datetime.now(timezone.utc)
        server.scope_store.add_membership(Membership(principal_id=server.identity.principal_id,
            scope_id=server.director.household_id, role="member", capabilities=("write",),
            valid_from=now - timedelta(days=2), valid_until=now - timedelta(days=1)))
        assert server.diagnostics.receipts() == {"ok": False, "error": "scope_denied"}
    finally:
        server.server_close()
