"""`ComputerActionService`: authorization + consequence verification in
front of `FilesystemProvider.execute()` -- the seam that was previously
unreachable from anywhere in the running application."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.actions import ActionLedgerStore
from haven.core.domain import DecisionStatus
from haven.resources import ResourceStore
from haven.web.computer_actions import ComputerActionService
from haven.web.demo import DemoDirector
from haven.web.setup_config import SetupConfigStore
from haven.web.setup_service import SetupService

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _harness(data_dir: Path):
    store = SetupConfigStore(data_dir / "haven.json")
    director = DemoDirector(clock=lambda: NOW)
    resources = ResourceStore(data_dir / "resources.db")
    ledger = ActionLedgerStore(data_dir / "action_ledger.db")
    setup = SetupService(store=store, director=director, clock=lambda: NOW, resource_store=resources)
    actions = ComputerActionService(
        store=store, director=director, resource_store=resources, ledger=ledger, clock=lambda: NOW
    )
    return setup, actions, resources, ledger, director


def test_request_action_fails_cleanly_when_computer_access_is_not_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        _, actions, _, _, _ = _harness(data_dir)

        result = actions.request_action(
            action="filesystem.create_folder",
            parameters={"path": str(Path(tmp) / "New Folder")},
            justification="user requested",
        )
    assert result["ok"] is False


def test_a_safe_action_executes_immediately_and_updates_the_resource_store():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)

        new_folder = allowed / "New Folder"
        result = actions.request_action(
            action="filesystem.create_folder",
            parameters={"path": str(new_folder)},
            justification="user requested via System > Files",
        )

        assert result["ok"] is True
        assert result["success"] is True
        assert new_folder.is_dir()
        saved = [r for r in resources.list_all() if r.title == "New Folder"]
        assert len(saved) == 1
        assert saved[0].stale is False

        entries = ledger.list_by_household(director.household_id)
        assert len(entries) == 1
        assert entries[0].status == DecisionStatus.ALLOW
        assert entries[0].success is True


def test_a_risky_action_requires_confirmation_before_it_touches_anything():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        source = allowed / "notes.txt"
        source.write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)
        setup.scan_computer_provider()
        source_id = next(r.resource_id for r in resources.list_all() if r.title == "notes.txt")

        destination = allowed / "moved.txt"
        result = actions.request_action(
            action="filesystem.move",
            resource_id=source_id,
            parameters={"source": str(source), "destination": str(destination)},
            justification="user requested via System > Files",
        )

        assert result["ok"] is True
        assert result["status"] == "confirmation_required"
        assert source.exists()
        assert not destination.exists()

        entries = ledger.list_by_household(director.household_id)
        assert len(entries) == 1
        assert entries[0].status == DecisionStatus.CONFIRMATION_REQUIRED
        assert entries[0].success is None


def test_confirming_a_pending_action_executes_it_and_verifies_the_consequence():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        source = allowed / "notes.txt"
        source.write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)
        setup.scan_computer_provider()
        source_id = next(r.resource_id for r in resources.list_all() if r.title == "notes.txt")

        destination = allowed / "moved.txt"
        submitted = actions.request_action(
            action="filesystem.move",
            resource_id=source_id,
            parameters={"source": str(source), "destination": str(destination)},
            justification="user requested via System > Files",
        )
        request_id = submitted["request_id"]

        confirmed = actions.confirm_action(request_id=request_id)

        assert confirmed["ok"] is True
        assert confirmed["success"] is True
        assert destination.exists()
        assert not source.exists()

        # Consequence verification: the resource store reflects the move
        # right away, without a rescan.
        assert resources.get(source_id).stale is True
        moved = [r for r in resources.list_all() if r.title == "moved.txt"]
        assert len(moved) == 1
        assert moved[0].stale is False

        entries = ledger.list_by_household(director.household_id)
        assert len(entries) == 2  # the original confirmation_required attempt, then the executed one
        assert any(e.status == DecisionStatus.ALLOW and e.success is True for e in entries)


def test_denying_a_pending_action_leaves_the_filesystem_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        source = allowed / "notes.txt"
        source.write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)
        setup.scan_computer_provider()
        source_id = next(r.resource_id for r in resources.list_all() if r.title == "notes.txt")

        destination = allowed / "moved.txt"
        submitted = actions.request_action(
            action="filesystem.move",
            resource_id=source_id,
            parameters={"source": str(source), "destination": str(destination)},
            justification="user requested via System > Files",
        )

        denied = actions.deny_action(request_id=submitted["request_id"])

        assert denied["ok"] is True
        assert source.exists()
        assert not destination.exists()
        # A second confirm against the now-dropped request must fail cleanly.
        assert actions.confirm_action(request_id=submitted["request_id"])["ok"] is False


def test_an_unjustified_request_is_denied_and_never_touches_the_filesystem():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)

        new_folder = allowed / "New Folder"
        result = actions.request_action(
            action="filesystem.create_folder", parameters={"path": str(new_folder)}, justification=""
        )

        assert result["ok"] is False
        assert not new_folder.exists()


def test_a_resource_id_that_does_not_match_the_source_parameter_is_denied():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        real = allowed / "real.txt"
        real.write_text("hello")
        decoy = allowed / "decoy.txt"
        decoy.write_text("world")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)
        setup.scan_computer_provider()
        real_id = next(r.resource_id for r in resources.list_all() if r.title == "real.txt")

        result = actions.request_action(
            action="filesystem.move",
            resource_id=real_id,
            parameters={"source": str(decoy), "destination": str(allowed / "elsewhere.txt")},
            justification="user requested via System > Files",
        )

        assert result["ok"] is False
        assert decoy.exists()


def test_an_unknown_resource_id_is_denied():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)

        result = actions.request_action(
            action="filesystem.copy",
            resource_id="file:does-not-exist",
            parameters={"source": str(allowed / "ghost.txt"), "destination": str(allowed / "ghost-copy.txt")},
            justification="user requested via System > Files",
        )

        assert result["ok"] is False


def test_a_stale_resource_id_is_denied():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        source = allowed / "notes.txt"
        source.write_text("hello")
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)
        setup.scan_computer_provider()
        source_id = next(r.resource_id for r in resources.list_all() if r.title == "notes.txt")
        resources.mark_stale(source_id)

        result = actions.request_action(
            action="filesystem.copy",
            resource_id=source_id,
            parameters={"source": str(source), "destination": str(allowed / "copy.txt")},
            justification="user requested via System > Files",
        )

        assert result["ok"] is False


def test_history_lists_every_attempt_most_recent_first():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        setup, actions, resources, ledger, director = _harness(data_dir)
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)

        actions.request_action(
            action="filesystem.create_folder",
            parameters={"path": str(allowed / "A")},
            justification="first",
        )
        actions.request_action(
            action="filesystem.create_folder",
            parameters={"path": str(allowed / "B")},
            justification="second",
        )

        history = actions.history()
    assert history["ok"] is True
    assert [entry["justification"] for entry in history["entries"]] == ["second", "first"]


def test_no_owner_declared_refuses_every_action():
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "Documents"
        allowed.mkdir()
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        store = SetupConfigStore(data_dir / "haven.json")
        # `DemoDirector` always carries a concrete declared owner; the gate
        # itself only reads the plain `has_declared_owner` attribute
        # (`HavenApplication.device_command` reads the same flag the same
        # way), so flipping it directly exercises the gate without needing
        # a real ownerless household composition.
        director = DemoDirector(clock=lambda: NOW)
        director.has_declared_owner = False
        resources = ResourceStore(data_dir / "resources.db")
        ledger = ActionLedgerStore(data_dir / "action_ledger.db")
        setup = SetupService(store=store, director=director, clock=lambda: NOW, resource_store=resources)
        actions = ComputerActionService(
            store=store, director=director, resource_store=resources, ledger=ledger, clock=lambda: NOW
        )
        setup.add_computer_provider_root(path=str(allowed))
        setup.set_computer_provider_enabled(enabled=True)

        result = actions.request_action(
            action="filesystem.create_folder",
            parameters={"path": str(allowed / "New Folder")},
            justification="user requested",
        )

    assert result["ok"] is False
    assert "owner" in result["error"]
