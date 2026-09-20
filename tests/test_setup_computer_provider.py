"""SetupService's computer/filesystem provider endpoints: enable, name
allowed folders, scan -- the setup-wizard-facing half of
`haven.integrations.computer.FilesystemProvider`.
"""

from __future__ import annotations

from dataclasses import replace
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.resources import ResourceStore
from haven.web.computer_provider import load_computer_provider_config
from haven.web.demo import DemoDirector
from haven.web.setup_config import SetupConfigStore
from haven.web.setup_service import SetupService

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _service(data_dir: Path, *, resource_store=None) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(
        store=store, director=DemoDirector(clock=lambda: NOW), clock=lambda: NOW, resource_store=resource_store
    )


def test_computer_status_defaults_to_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        status = service.status()
    assert status["setup"]["computer"] == {"enabled": False, "allowed_roots": [], "read_only": True}


def test_enabling_computer_access_keeps_the_default_read_only():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        service = _service(data_dir)
        service.add_computer_provider_root(path=str(allowed))

        result = service.set_computer_provider_enabled(enabled=True)

    assert result["ok"] is True
    assert result["setup"]["computer"]["read_only"] is True


def test_file_organization_requires_explicit_write_opt_in():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        service = _service(data_dir)
        service.add_computer_provider_root(path=str(allowed))

        result = service.set_computer_provider_enabled(enabled=True, read_only=False)

    assert result["ok"] is True
    assert result["setup"]["computer"]["read_only"] is False


def test_enabling_without_any_allowed_root_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.set_computer_provider_enabled(enabled=True)
    assert result["ok"] is False
    assert "allowed folder" in result["error"]


def test_add_root_requires_a_real_existing_folder():
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.add_computer_provider_root(path=str(Path(tmp) / "does-not-exist"))
    assert result["ok"] is False
    assert "not a folder" in result["error"]


def test_add_root_then_enable_round_trips_through_status():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        service = _service(data_dir)

        add_result = service.add_computer_provider_root(path=str(allowed))
        assert add_result["ok"] is True
        assert str(allowed.resolve()) in add_result["setup"]["computer"]["allowed_roots"]

        enable_result = service.set_computer_provider_enabled(enabled=True)
        assert enable_result["ok"] is True
        assert enable_result["setup"]["computer"]["enabled"] is True


def test_adding_the_same_root_twice_is_a_no_op():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        service = _service(data_dir)

        service.add_computer_provider_root(path=str(allowed))
        result = service.add_computer_provider_root(path=str(allowed))
    assert result["setup"]["computer"]["allowed_roots"] == [str(allowed.resolve())]


def test_removing_the_last_root_automatically_disables():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        service = _service(data_dir)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)

        result = service.remove_computer_provider_root(path=str(allowed.resolve()))
    assert result["setup"]["computer"]["enabled"] is False
    assert result["setup"]["computer"]["allowed_roots"] == []


def test_scan_without_being_enabled_fails_cleanly():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = ResourceStore(data_dir / "resources.db")
        service = _service(data_dir, resource_store=store)

        result = service.scan_computer_provider()
    assert result["ok"] is False


def test_scan_persists_real_resources_into_the_store():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        (allowed / "notes.txt").write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = ResourceStore(data_dir / "resources.db")
        service = _service(data_dir, resource_store=store)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)

        result = service.scan_computer_provider()

        assert result["ok"] is True
        assert result["scanned"] >= 2  # the folder itself plus notes.txt
        saved = store.list_all()
        assert any(r.title == "notes.txt" for r in saved)


def test_removing_a_root_immediately_stales_its_resources_in_the_store():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        (allowed / "notes.txt").write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = ResourceStore(data_dir / "resources.db")
        service = _service(data_dir, resource_store=store)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)
        service.scan_computer_provider()
        assert any(not r.stale for r in store.list_all())

        service.remove_computer_provider_root(path=str(allowed.resolve()))

        # No rescan happened -- removal alone must hide the folder's
        # resources from a search-facing view without waiting for one.
        assert all(r.stale for r in store.list_all())


def test_rescanning_after_a_file_is_deleted_stales_it_instead_of_leaving_it_current():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        doomed = allowed / "notes.txt"
        doomed.write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = ResourceStore(data_dir / "resources.db")
        service = _service(data_dir, resource_store=store)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)
        service.scan_computer_provider()
        notes_id = next(r.resource_id for r in store.list_all() if r.title == "notes.txt")
        assert store.get(notes_id).stale is False

        doomed.unlink()
        result = service.scan_computer_provider()

        assert result["ok"] is True
        assert result["staled"] >= 1
        assert store.get(notes_id).stale is True
        # Reconciliation staled the old record rather than the scan
        # silently leaving a second, live-looking copy behind.
        assert len([r for r in store.list_all() if r.title == "notes.txt"]) == 1


def test_rescanning_a_still_present_file_keeps_it_fresh():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        (allowed / "notes.txt").write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = ResourceStore(data_dir / "resources.db")
        service = _service(data_dir, resource_store=store)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)
        service.scan_computer_provider()

        result = service.scan_computer_provider()

        assert result["ok"] is True
        assert result["staled"] == 0
        assert all(not r.stale for r in store.list_all())


def test_rescan_reconciles_legacy_unsafe_open_capability():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        executable = allowed / "installer.exe"
        executable.write_text("not an executable")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = ResourceStore(data_dir / "resources.db")
        service = _service(data_dir, resource_store=store)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)
        service.scan_computer_provider()

        current = next(r for r in store.list_all() if r.title == "installer.exe")
        assert "filesystem.open" not in current.capabilities

        # Simulate a row written by an older HAVEN version, before unsafe
        # file extensions were excluded from the open capability.
        store.save(replace(current, capabilities=current.capabilities + ("filesystem.open",)))
        assert "filesystem.open" in store.get(current.resource_id).capabilities

        result = service.scan_computer_provider()

        assert result["ok"] is True
        refreshed = store.get(current.resource_id)
        assert refreshed is not None
        assert "filesystem.open" not in refreshed.capabilities


def test_config_survives_a_fresh_setup_service_over_the_same_directory():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        service = _service(data_dir)
        service.add_computer_provider_root(path=str(allowed))
        service.set_computer_provider_enabled(enabled=True)

        reloaded = load_computer_provider_config(SetupConfigStore(data_dir / "haven.json"))
    assert reloaded.enabled is True
    assert reloaded.allowed_roots == (str(allowed.resolve()),)
