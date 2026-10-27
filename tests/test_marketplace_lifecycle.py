"""Local acquisition, failure recovery, and live host-boundary tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.marketplace import LocalProductManager, LocalRelease, ProductError, create_local_fixture_manager
from haven.marketplace import lifecycle
from haven.web.marketplace_lifecycle_api import product_action
from test_marketplace_catalog import _sign, keypair  # reuse existing ephemeral test trust

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parents[1] / "haven" / "marketplace" / "fixtures"


def test_host_owned_storage_supports_long_windows_paths(tmp_path):
    root = tmp_path.joinpath(*(["synthetic-long-state-segment-" + "x" * 40] * 4))
    manager = create_local_fixture_manager(root, authorize=lambda principal, operation: principal == "owner")
    _acquire(manager)
    manager.set_enabled("haven-welcome", True, principal_id="owner")
    assert manager.launch("haven-welcome", principal_id="owner")["observed_healthy"]
    reopened = create_local_fixture_manager(root, authorize=lambda principal, operation: principal == "owner")
    assert reopened.status("haven-welcome")["installed_version"] == "1.0.0"


@pytest.fixture
def rig(tmp_path, keypair):
    private, public = keypair
    payload = json.loads((FIXTURES / "catalog.json").read_bytes())
    payload.pop("signature")
    payload["expires_at"] = (NOW + timedelta(days=30)).isoformat()
    pins = []
    for version in ("1.0.0", "1.1.0"):
        source = tmp_path / f"source-{version}.json"
        source.write_bytes((FIXTURES / f"welcome-{version}.json").read_bytes())
        raw = source.read_bytes()
        pins.append(LocalRelease("haven-welcome", version, "any", hashlib.sha256(raw).hexdigest(), len(raw), source))
    values = {"catalog": _sign(payload, private), "keys": {"marketplace-test-key": public}, "authorized": True, "now": NOW}
    def make(**overrides):
        options = dict(catalog=lambda: values["catalog"], trusted_keys=lambda: values["keys"], releases=tuple(pins), platform="any", authorize=lambda principal, operation: principal == "owner" and values["authorized"], clock=lambda: values["now"])
        options.update(overrides)
        return LocalProductManager(tmp_path / "products", **options)
    manager = make()
    return manager, values, payload, private, pins, make


def _acquire(manager, version="1.0.0"):
    manager.download("haven-welcome", version, principal_id="owner")
    return manager.install("haven-welcome", version, principal_id="owner")


def _enabled(manager, version="1.0.0"):
    _acquire(manager, version)
    manager.set_enabled("haven-welcome", True, principal_id="owner")
    return manager.launch("haven-welcome", principal_id="owner")


def test_bundled_fixture_runs_full_local_lifecycle_without_network(tmp_path):
    manager = create_local_fixture_manager(tmp_path / "products", authorize=lambda principal, op: principal == "owner")
    status = manager.status("haven-welcome")
    assert all(r["available"] and not r["downloaded"] for r in status["releases"])
    assert not status["installed"] and not status["enabled"] and not status["observed_healthy"]
    status = manager.download("haven-welcome", "1.0.0", principal_id="owner")
    assert status["releases"][0]["downloaded"] and not status["installed"]
    status = manager.install("haven-welcome", "1.0.0", principal_id="owner")
    assert status["installed"] and not status["enabled"] and not status["observed_healthy"]
    manager.set_enabled("haven-welcome", True, principal_id="owner")
    assert not manager.status("haven-welcome")["observed_healthy"]
    assert "Welcome to HAVEN" in manager.launch("haven-welcome", principal_id="owner")["message"]
    assert manager.status("haven-welcome")["observed_healthy"]
    _acquire(manager, "1.1.0")
    assert manager.status("haven-welcome")["rollback_versions"] == ["1.0.0"]
    status = manager.rollback("haven-welcome", "1.0.0", principal_id="owner")
    assert status["installed_version"] == "1.0.0" and not status["enabled"]
    data = manager.root / "data" / "haven-welcome"
    data.mkdir(parents=True)
    (data / "note.txt").write_text("local note")
    status = manager.uninstall("haven-welcome", principal_id="owner")
    assert not status["installed"] and not status["enabled"] and status["data_preserved"]
    assert (data / "note.txt").read_text() == "local note"
    assert not (manager.root / "releases" / "haven-welcome").exists()
    assert not any(r["downloaded"] for r in status["releases"])
    manager.uninstall("haven-welcome", principal_id="owner", data_policy="delete")
    assert not data.exists()


@pytest.mark.parametrize("kind,code", [("key", "signature_untrusted"), ("signature", "signature_invalid"), ("stale", "expired"), ("revoked", "release_revoked"), ("version", "version_mismatch"), ("digest", "digest_mismatch"), ("platform", "platform_mismatch")])
def test_current_trust_defeats_installed_and_enabled_state(rig, kind, code):
    manager, values, payload, private, _, _ = rig
    _enabled(manager)
    if kind == "key":
        values["keys"] = {}
    elif kind == "signature":
        values["catalog"]["items"][0]["display_name"] = "tampered"
    elif kind == "stale":
        values["now"] = NOW + timedelta(days=31)
    else:
        if kind == "revoked":
            payload["items"][0]["release_state"] = "security_hold"
        elif kind == "version":
            payload["items"][0]["distribution"][0]["release_id"] = "9.9.9"
        elif kind == "digest":
            payload["items"][0]["distribution"][0]["sha256"] = "0" * 64
        elif kind == "platform":
            payload["items"][0]["distribution"][0]["platforms"] = ["another-platform"]
        values["catalog"] = _sign(payload, private)
    with pytest.raises(ProductError) as exc:
        manager.launch("haven-welcome", principal_id="owner")
    assert exc.value.code == code
    status = manager.status("haven-welcome")
    assert status["installed_version"] == "1.0.0"
    assert not status["enabled"] and not status["observed_healthy"]
    manager.set_enabled("haven-welcome", False, principal_id="owner")
    manager.uninstall("haven-welcome", principal_id="owner")


@pytest.mark.parametrize("product,version", [("../escape", "1.0.0"), ("haven-welcome", "../../escape"), ("haven-welcome", "latest"), ("haven-welcome", "9.0.0"), ("unknown", "1.0.0")])
def test_unapproved_identifiers_never_become_storage_paths(rig, product, version):
    manager, *_ = rig
    with pytest.raises(ValueError):
        manager.download(product, version, principal_id="owner")
    assert not (manager.root / "downloads").exists()


def test_wrong_host_platform_and_source_digest_are_rejected(rig):
    manager, _, _, _, pins, make = rig
    with pytest.raises(ProductError, match="platform"):
        make(platform="different").download("haven-welcome", "1.0.0", principal_id="owner")
    pins[0].source.write_bytes(b"x" * pins[0].size_bytes)
    with pytest.raises(ProductError) as exc:
        manager.download("haven-welcome", "1.0.0", principal_id="owner")
    assert exc.value.code == "digest_mismatch"


def test_inspection_rejects_path_and_command_fields_even_when_host_pinned(rig):
    manager, values, payload, private, pins, make = rig
    raw = json.loads(pins[0].source.read_bytes())
    raw["install_command"] = "never execute"
    raw["files"] = {"../../escape": "bad"}
    content = json.dumps(raw).encode()
    pins[0].source.write_bytes(content)
    pin = LocalRelease("haven-welcome", "1.0.0", "any", hashlib.sha256(content).hexdigest(), len(content), pins[0].source)
    payload["items"][0]["distribution"][0].update(sha256=pin.sha256, size_bytes=pin.size_bytes)
    values["catalog"] = _sign(payload, private)
    with pytest.raises(ProductError) as exc:
        make(releases=(pin,)).download("haven-welcome", "1.0.0", principal_id="owner")
    assert exc.value.code == "artifact_invalid"


def test_duplicate_clicks_and_two_manager_instances_are_idempotent(rig):
    manager, _, _, _, _, make = rig
    other = make()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: _acquire(manager if i % 2 else other), range(8)))
    assert all(row["installed_version"] == "1.0.0" for row in results)
    assert json.loads((manager.root / "registry.json").read_bytes())["products"]["haven-welcome"]["versions"] == ["1.0.0"]


def test_live_authorization_revocation_blocks_old_manager(rig):
    manager, values, *_ = rig
    _enabled(manager)
    values["authorized"] = False
    result = product_action(manager, "launch", {"product_id": "haven-welcome", "principal_id": "owner"}, principal_id="intruder")
    assert result["error"] == "forbidden"
    for action in (lambda: manager.launch("haven-welcome", principal_id="owner"), lambda: manager.install("haven-welcome", "1.1.0", principal_id="owner")):
        with pytest.raises(ProductError) as exc:
            action()
        assert exc.value.code == "forbidden"


def test_observed_health_cannot_survive_another_instance_reinstall(rig):
    manager, _, _, _, _, make = rig
    _enabled(manager)
    other = make()
    other.uninstall("haven-welcome", principal_id="owner")
    _acquire(other)
    other.set_enabled("haven-welcome", True, principal_id="owner")
    assert not manager.status("haven-welcome")["observed_healthy"]


def test_foreign_principal_cannot_cancel_an_active_download(rig, monkeypatch):
    manager, *_ = rig
    manager._authorize = lambda principal, operation: principal in {"owner", "other"}
    original = manager._inspect
    def foreign_cancel(raw, release):
        assert not manager.cancel("owned-operation", principal_id="other")["cancel_requested"]
        return original(raw, release)
    monkeypatch.setattr(manager, "_inspect", foreign_cancel)
    result = manager.download("haven-welcome", "1.0.0", principal_id="owner", operation_id="owned-operation")
    assert result["releases"][0]["downloaded"]


def test_cancel_during_download_never_commits_partial_bytes(rig, monkeypatch):
    manager, *_ = rig
    original = manager._inspect
    def cancel_before_commit(raw, release):
        manager.cancel("click-1", principal_id="owner")
        return original(raw, release)
    monkeypatch.setattr(manager, "_inspect", cancel_before_commit)
    with pytest.raises(ProductError) as exc:
        manager.download("haven-welcome", "1.0.0", principal_id="owner", operation_id="click-1")
    assert exc.value.code == "cancelled"
    assert not manager._download_path(manager._releases[("haven-welcome", "1.0.0")]).exists()


@pytest.mark.parametrize("failure", ["download", "stage", "pointer"])
def test_interrupted_update_preserves_last_good_install(rig, monkeypatch, failure):
    manager, _, _, _, _, make = rig
    _enabled(manager)
    original_atomic = manager._atomic_bytes
    if failure != "download":
        manager.download("haven-welcome", "1.1.0", principal_id="owner")
    def fail(path, raw):
        if (failure == "download" and path.parent.name == "downloads") or (failure == "stage" and path.name == "package.json") or (failure == "pointer" and path.name == "registry.json"):
            raise OSError("simulated interruption")
        return original_atomic(path, raw)
    monkeypatch.setattr(manager, "_atomic_bytes", fail)
    with pytest.raises(OSError):
        if failure == "download":
            manager.download("haven-welcome", "1.1.0", principal_id="owner")
        else:
            manager.install("haven-welcome", "1.1.0", principal_id="owner")
    restarted = make()
    assert restarted.status("haven-welcome")["installed_version"] == "1.0.0"
    assert restarted.status("haven-welcome")["enabled"]
    assert not restarted.status("haven-welcome")["observed_healthy"]
    assert restarted.launch("haven-welcome", principal_id="owner")["version"] == "1.0.0"


def test_corrupt_update_and_corrupt_installed_content_fail_closed(rig):
    manager, _, _, _, pins, _ = rig
    _enabled(manager)
    pins[1].source.write_bytes(b"x" * pins[1].size_bytes)
    with pytest.raises(ProductError):
        manager.download("haven-welcome", "1.1.0", principal_id="owner")
    assert manager.launch("haven-welcome", principal_id="owner")["version"] == "1.0.0"
    manager._installed_path(pins[0]).write_bytes(b"x" * pins[0].size_bytes)
    assert not manager.status("haven-welcome")["enabled"]
    with pytest.raises(ProductError):
        manager.launch("haven-welcome", principal_id="owner")


def test_dependencies_do_not_auto_install_or_enable(rig):
    manager, _, _, _, pins, make = rig
    pin = LocalRelease(pins[1].product_id, pins[1].version, pins[1].platform, pins[1].sha256, pins[1].size_bytes, pins[1].source, dependencies=(("haven-welcome", "1.0.0"),))
    dependent = make(releases=(pins[0], pin))
    dependent.download("haven-welcome", "1.1.0", principal_id="owner")
    with pytest.raises(ProductError) as exc:
        dependent.install("haven-welcome", "1.1.0", principal_id="owner")
    assert exc.value.code == "dependency_missing"
    assert not dependent.status("haven-welcome")["installed"]


def test_restart_discards_incomplete_stage_and_does_not_claim_health(rig):
    manager, _, _, _, _, make = rig
    _enabled(manager)
    stage = manager.root / "staging" / "interrupted"
    stage.mkdir(parents=True)
    (stage / "package.json").write_text("partial")
    restarted = make()
    assert not stage.exists()
    status = restarted.status("haven-welcome")
    assert status["installed"] and status["enabled"] and not status["observed_healthy"]


def test_process_death_releases_lock_and_recovers_incomplete_candidates(rig):
    manager, _, _, _, _, make = rig
    _enabled(manager)
    # Kill only this test helper process while it holds the real OS lock.
    # The installed pointer must survive and startup must discard partial files.
    code = """
import os, sys
from haven.marketplace import create_local_fixture_manager
manager = create_local_fixture_manager(sys.argv[1], authorize=lambda principal, op: True)
with manager._transaction():
    stage = manager.root / 'staging' / 'interrupted'
    stage.mkdir(parents=True)
    (stage / 'package.json').write_text('partial')
    (manager.root / 'downloads' / 'interrupted.tmp').write_text('partial')
    os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(manager.root)], check=True, timeout=20)
    assert (manager.root / "staging" / "interrupted").exists()
    restarted = make()
    assert not (manager.root / "staging").exists()
    assert not (manager.root / "downloads" / "interrupted.tmp").exists()
    assert restarted.status("haven-welcome")["installed_version"] == "1.0.0"
    assert restarted.launch("haven-welcome", principal_id="owner")["observed_healthy"]


def test_removal_interruption_is_unregistered_and_resumed(rig, monkeypatch):
    manager, _, _, _, _, make = rig
    _enabled(manager)
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("locked")))
        with pytest.raises(ProductError) as exc:
            manager.uninstall("haven-welcome", principal_id="owner")
        assert exc.value.code == "removal_pending"
        assert not manager.status("haven-welcome")["installed"]
    restarted = make()
    assert not restarted.status("haven-welcome")["removal_pending"]
    assert not (manager.root / "releases" / "haven-welcome").exists()


@pytest.mark.parametrize("legacy_checkpoint", [False, True])
def test_removal_checkpoint_cannot_delete_data_without_fresh_actor_authorization(rig, monkeypatch, legacy_checkpoint):
    manager, values, _, _, _, make = rig
    _enabled(manager)
    data = manager.root / "data" / "haven-welcome"
    data.mkdir(parents=True)
    note = data / "note.txt"
    note.write_text("retained after revocation")
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("locked")))
        with pytest.raises(ProductError):
            manager.uninstall("haven-welcome", principal_id="owner", data_policy="delete")
    registry = manager.root / "registry.json"
    if legacy_checkpoint:
        saved = json.loads(registry.read_bytes())
        saved["products"]["haven-welcome"].pop("removal_principal_id")
        registry.write_text(json.dumps(saved))
    else:
        values["authorized"] = False
    restarted = make()
    assert restarted.status("haven-welcome")["removal_pending"]
    assert note.read_text() == "retained after revocation"
    values["authorized"] = True
    restarted.uninstall("haven-welcome", principal_id="owner", data_policy="delete")
    assert not data.exists()


def test_symlink_sources_and_storage_are_rejected(rig, tmp_path):
    manager, _, _, _, pins, make = rig
    original = tmp_path / "original.json"
    original.write_bytes(pins[0].source.read_bytes())
    pins[0].source.unlink()
    try:
        pins[0].source.symlink_to(original)
    except OSError:
        # Windows junctions also exercise the no-reparse-point boundary when
        # unprivileged file symlink creation is unavailable.
        pins[0].source.write_bytes(original.read_bytes())
        destination = manager.root / "downloads"
        outside = tmp_path / "outside"
        outside.mkdir()
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(destination), str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip("host cannot create a symlink or junction fixture")
    with pytest.raises(ProductError) as exc:
        manager.download("haven-welcome", "1.0.0", principal_id="owner")
    assert exc.value.code == "unsafe_path"


def test_registry_corruption_is_reported_and_preserved(rig):
    manager, _, _, _, _, make = rig
    _acquire(manager)
    registry = manager.root / "registry.json"
    registry.write_text("partial")
    with pytest.raises(ProductError) as exc:
        make()
    assert exc.value.code == "registry_invalid"
    assert registry.read_text() == "partial"


def test_api_exact_version_policy_and_safe_error_projection(rig):
    manager, *_ = rig
    assert product_action(manager, "install", {"product_id": "haven-welcome", "version": "1.0.0"}, principal_id="owner")["error"] == "download_required"
    assert product_action(manager, "download", {"product_id": "haven-welcome", "version": "latest"}, principal_id="owner")["error"] == "release_invalid"
    assert product_action(manager, "uninstall", {"product_id": "haven-welcome", "data_policy": "guess"}, principal_id="owner")["error"] == "request_invalid"
    assert product_action(manager, "execute", {"command": "anything"}, principal_id="owner")["error"] == "action_invalid"
