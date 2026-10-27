"""Real server/IPC composition, durable lifecycle and current-authority checks."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import threading

from haven.ipc import request_message
from haven.models.bridge import _system_message
from haven.web.server import make_server


NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
SESSION = "synthetic-local-workflow-session"


@contextmanager
def boot(path):
    server, _ = make_server(0, data_dir=str(path), clock=lambda: NOW, session_token=SESSION)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def http(server, method, path, payload=None, *, authenticated=True):
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=15)
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["Cookie"] = f"haven_session={SESSION}"
    connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
    response = connection.getresponse()
    status, body = response.status, json.loads(response.read())
    connection.close()
    return status, body


def ipc(dispatcher, method, **params):
    response = dispatcher(request_message(f"test:{method}", method, params))
    assert response["ok"], response
    return response["result"]


def owner(server):
    status, body = http(server, "POST", "/api/setup/household/people", {"name": "Sample Owner", "role": "owner"})
    assert status == 200 and body["ok"], body


def propose(server):
    status, body = http(server, "POST", "/api/decisions/propose", {
        "expected_generation": 0, "problem": "Choose a reminder format",
        "options": [["plain", "Plain text"], ["card", "A card"]],
        "rationale": "Keep reminders accessible", "reconsideration_triggers": ["A reader needs a different format"],
    })
    assert status == 200 and body["ok"], body
    return body["decision"]["decision_id"]


def feature(dispatcher, action, version=None):
    return ipc(dispatcher, "features.action", action=action, feature_id="haven.decision-context",
               version=version, command="inspect", parameters={})


def test_memory_features_real_dispatch_restart_history_and_rebuild(tmp_path):
    with boot(tmp_path) as server:
        dispatcher = server.build_ipc_dispatcher()  # Deliberately predates setup rebuild.
        assert http(server, "GET", "/api/decisions", authenticated=False)[0] == 401
        assert not feature(dispatcher, "stage", "1.0.0")["ok"]
        owner(server)
        decision_id = propose(server)
        needs = ipc(dispatcher, "needs_you.list")
        assert any(item["source_ref"] == decision_id for item in needs["items"])
        accepted = ipc(dispatcher, "decisions.review", decision_id=decision_id, expected_generation=1,
                       expected_revision=1, status="accepted", selected_option_id="plain",
                       rationale="The owner chose the readable format", review_ref="human-review:sample")
        assert accepted["ok"], accepted
        assert feature(dispatcher, "stage", "1.0.0")["ok"]
        assert feature(dispatcher, "install")["ok"]
        assert feature(dispatcher, "enable")["ok"]
        assert feature(dispatcher, "run")["result"]["decisions"][0]["decision_id"] == decision_id
        assert feature(dispatcher, "stage", "1.1.0")["ok"]
        assert feature(dispatcher, "update")["version"] == "1.1.0"
        assert feature(dispatcher, "rollback")["version"] == "1.0.0"
        context = server.director._agent_context(focus=None, now=NOW)
        assert context.decision_memory.to_dict()["decisions"][0]["decision_id"] == decision_id
        assert "evidence only" in _system_message(context)
        old = server.local_workflows
        server.rebuild_director()
        assert old._closed
        assert feature(dispatcher, "run")["result"]["decisions"][0]["decision_id"] == decision_id
        assert any(event.event_type.value == "local_lifecycle_changed" for event in server.director.store.events)
    with boot(tmp_path) as server:
        dispatcher = server.build_ipc_dispatcher()
        snapshot = ipc(dispatcher, "decisions.snapshot")["snapshot"]
        assert len(snapshot["decisions"][0]["revisions"]) == 2
        assert feature(dispatcher, "run")["ok"]
        assert feature(dispatcher, "disable")["ok"]
        assert not feature(dispatcher, "run")["ok"]
        assert feature(dispatcher, "uninstall")["ok"]


def product(server, action, version=None, **extra):
    status, body = http(server, "POST", f"/api/marketplace/products/{action}",
                        {"product_id": "haven-welcome", "version": version, **extra})
    assert status == 200 and body["ok"], body
    return body["result"]


def test_products_http_ipc_upgrade_rollback_restart_and_remove(tmp_path):
    with boot(tmp_path) as server:
        owner(server)
        dispatcher = server.build_ipc_dispatcher()
        assert http(server, "GET", "/api/marketplace/products")[1]["local_only"]
        for version in ("1.0.0", "1.1.0"):
            product(server, "download", version, operation_id=f"sample-download-{version.replace('.', '')}")
        product(server, "install", "1.0.0")
        product(server, "enable")
        assert product(server, "launch")["observed_healthy"]
        product(server, "update", "1.1.0")
        product(server, "enable")
        assert product(server, "launch")["version"] == "1.1.0"
        product(server, "rollback", "1.0.0")
        product(server, "enable")
        assert product(server, "launch")["version"] == "1.0.0"
        assert ipc(dispatcher, "marketplace.products")["products"][0]["integration_enabled"] is False
    with boot(tmp_path) as server:
        rows = http(server, "GET", "/api/marketplace/products")[1]["products"]
        assert rows[0]["observed_healthy"] is False
        product(server, "launch")
        assert product(server, "uninstall", data_policy="preserve")["data_preserved"]
        assert not product(server, "uninstall", data_policy="delete")["data_preserved"]


def test_revocation_defeats_stale_dispatcher_checkpoint_and_service_handle(tmp_path):
    with boot(tmp_path) as server:
        owner(server)
        dispatcher = server.build_ipc_dispatcher()
        propose(server)
        feature(dispatcher, "stage", "1.0.0")
        feature(dispatcher, "install")
        feature(dispatcher, "enable")
        scope_id = server.identity.personal_scope_id
        server.scope_store.save_scope(replace(server.scope_store.get_scope(scope_id), status="archived"))
        assert not feature(dispatcher, "run")["ok"]
        refused = ipc(dispatcher, "decisions.review", decision_id="forged", expected_generation=1,
                      expected_revision=1, status="accepted", rationale="Old checkpoint", review_ref="old",
                      selected_option_id="plain")
        assert not refused["ok"]
        assert not ipc(dispatcher, "marketplace.product", action="download", product_id="haven-welcome",
                       version="1.0.0", principal_id="forged-owner")["ok"]
        assert server.director._agent_context(focus=None, now=NOW).decision_memory is None


def test_data_move_and_backup_keep_decisions_features_and_product_content(tmp_path):
    original, moved = tmp_path / "original", tmp_path / "moved"
    with boot(original) as server:
        owner(server)
        decision_id = propose(server)
        product(server, "download", "1.0.0", operation_id="sample-before-move")
        product(server, "install", "1.0.0")
        dispatcher = server.build_ipc_dispatcher()
        status, result = http(server, "POST", "/api/setup/data-dir", {"path": str(moved)})
        assert status == 200 and result["ok"], result
        assert not (original / "decisions.db").exists()
        assert ipc(dispatcher, "decisions.snapshot")["snapshot"]["decisions"][0]["decision_id"] == decision_id
        assert ipc(dispatcher, "marketplace.products")["products"][0]["installed_version"] == "1.0.0"
        snapshot = server.backups.create()
        assert "decisions.db" in snapshot["files"] and "features.json" in snapshot["files"]
        assert any(name.startswith("marketplace-products/releases/") for name in snapshot["files"])


def test_corrupt_local_lifecycle_state_is_a_named_gate_without_core_boot_failure(tmp_path):
    with boot(tmp_path) as server:
        owner(server)
    (tmp_path / "features.json").write_text("broken JSON", encoding="utf-8")
    (tmp_path / "marketplace-products" / "registry.json").write_text("broken JSON", encoding="utf-8")
    with boot(tmp_path) as server:
        assert http(server, "GET", "/api/features")[1]["error"] == "feature_lifecycle_unavailable"
        assert http(server, "GET", "/api/marketplace/products")[1]["error"] == "product_lifecycle_unavailable"
        assert http(server, "GET", "/api/decisions")[1]["ok"]


def test_global_owner_cannot_review_decisions_in_a_member_only_scope(tmp_path):
    with boot(tmp_path) as server:
        owner(server)
        decision_id = propose(server)
        identity = server.identity
        membership = next(item for item in server.scope_store.memberships_for(identity.principal_id)
                          if item.scope_id == identity.personal_scope_id)
        server.scope_store.add_membership(replace(membership, role="member"))
        result = ipc(server.build_ipc_dispatcher(), "decisions.review", decision_id=decision_id,
                     expected_generation=1, expected_revision=1, status="accepted", selected_option_id="plain",
                     rationale="Attempt with an owner role outside this scope", review_ref="stale-review")
        assert not result["ok"]
        assert server.decisions.snapshot(identity.personal_scope_id).generation == 1


def test_product_receipt_scope_is_host_derived_and_history_failure_is_reconcilable(tmp_path, monkeypatch):
    with boot(tmp_path) as server:
        owner(server)
        product(server, "download", "1.0.0", operation_id="sample-scoped-download", scope_id="forged")
        event = server.director.store.events[-1]
        assert dict(event.payload)["scope_id"] == server.identity.personal_scope_id
        def unavailable(*_args):
            raise OSError("synthetic history unavailable")
        monkeypatch.setattr(server.local_workflows, "record", unavailable)
        status, result = http(server, "POST", "/api/decisions/propose", {
            "expected_generation": 0, "problem": "Choose a format", "options": [["plain", "Text"]],
            "rationale": "Readable"})
        assert status == 400 and result["error"] == "outcome_uncertain"
        assert server.decisions.snapshot(server.identity.personal_scope_id).generation == 1


def test_data_move_refuses_existing_installation_without_overwriting_any_file(tmp_path):
    destination = tmp_path / "occupied"
    destination.mkdir()
    original = destination / "haven.json"
    original.write_bytes(b"existing installation sentinel")
    with boot(tmp_path / "source") as server:
        owner(server)
        source_identity = server.identity.principal_id
        status, result = http(server, "POST", "/api/setup/data-dir", {"path": str(destination)})
        assert status == 400 and not result["ok"]
        assert original.read_bytes() == b"existing installation sentinel"
        assert server.identity.principal_id == source_identity
        assert server.setup_store.path.parent == tmp_path / "source"
