"""`FilesystemProvider`: confined observation and mutation of real files.

Every test uses a real temporary directory and real file I/O -- no mocking
the filesystem -- because the entire point of this provider is the
boundary between "inside the allowed roots" and "outside them", which a
mock would not meaningfully exercise.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.core.domain import DeviceCommand
from haven.integrations.computer import FilesystemProvider, PathOutsideAllowedRoots

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _command(service: str, **params) -> DeviceCommand:
    return DeviceCommand(
        request_id="r1",
        target_device_id="file:test",
        service=service,
        parameters=tuple(params.items()),
        requested_at=NOW,
    )


# -- construction -----------------------------------------------------------


def test_requires_at_least_one_allowed_root():
    with pytest.raises(ValueError, match="at least one"):
        FilesystemProvider(allowed_roots=(), scope_id="project:haven")


def test_rejects_a_root_that_does_not_exist():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="does not exist"):
            FilesystemProvider(allowed_roots=(Path(tmp) / "nope",), scope_id="project:haven")


def test_rejects_a_blank_scope_id():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="scope_id"):
            FilesystemProvider(allowed_roots=(tmp,), scope_id="  ")


# -- observation --------------------------------------------------------


def test_observes_files_and_folders_under_the_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        (root / "notes.txt").write_text("hello")
        (root / "sub").mkdir()
        (root / "sub" / "nested.txt").write_text("nested")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        records = provider.observe()

    by_locator = {r.locator: r for r in records}
    assert by_locator[str(root)].resource_type == "folder"
    assert by_locator[str(root / "notes.txt")].resource_type == "file"
    assert by_locator[str(root / "sub")].resource_type == "folder"
    assert by_locator[str(root / "sub" / "nested.txt")].resource_type == "file"


def test_files_get_a_real_content_hash_folders_do_not():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        (root / "a.txt").write_text("same content")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        records = {r.locator: r for r in provider.observe()}

    assert records[str(root)].content_hash is None
    assert records[str(root / "a.txt")].content_hash is not None
    assert len(records[str(root / "a.txt")].content_hash) == 64  # sha256 hex digest


def test_identical_content_produces_identical_hashes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        (root / "a.txt").write_text("same content")
        (root / "b.txt").write_text("same content")
        (root / "c.txt").write_text("different content")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        records = {r.locator: r for r in provider.observe()}

    assert records[str(root / "a.txt")].content_hash == records[str(root / "b.txt")].content_hash
    assert records[str(root / "a.txt")].content_hash != records[str(root / "c.txt")].content_hash


def test_resource_id_is_stable_across_two_observations():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        (root / "a.txt").write_text("x")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        first = {r.locator: r.resource_id for r in provider.observe()}
        second = {r.locator: r.resource_id for r in provider.observe()}
    assert first == second


def test_read_only_provider_reports_read_capability_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        (root / "a.txt").write_text("x")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", read_only=True, clock=lambda: NOW)
        records = {r.locator: r for r in provider.observe()}
    assert records[str(root / "a.txt")].capabilities == ("filesystem.read",)


def test_writable_provider_declares_mutation_capabilities():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        (root / "a.txt").write_text("x")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        records = {r.locator: r for r in provider.observe()}
    file_caps = records[str(root / "a.txt")].capabilities
    folder_caps = records[str(root)].capabilities
    assert "filesystem.copy" in file_caps
    assert "filesystem.create_folder" not in file_caps
    assert "filesystem.create_folder" in folder_caps


def test_max_entries_caps_a_large_tree_without_crashing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        for i in range(20):
            (root / f"file{i}.txt").write_text("x")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", max_entries=5, clock=lambda: NOW)
        records = provider.observe()
    assert len(records) == 5


def test_a_symlinked_directory_is_not_descended_into(tmp_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        outside = Path(tmp) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("should not appear")
        try:
            (root / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation requires elevated privilege on this host")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        locators = {r.locator for r in provider.observe()}
    assert str(outside / "secret.txt") not in locators


# -- execution: safety ----------------------------------------------------


def test_create_folder_outside_allowed_roots_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        outside = Path(tmp) / "outside"

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.create_folder", path=str(outside)))
        assert result.success is False
        assert "outside every allowed root" in result.detail
        assert not outside.exists()


def test_path_traversal_via_dotdot_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        escape_target = str(root / ".." / "escaped")
        result = provider.execute(_command("filesystem.create_folder", path=escape_target))
        assert result.success is False
        assert "outside every allowed root" in result.detail
        assert not (Path(tmp) / "escaped").exists()


def test_move_destination_outside_allowed_roots_is_rejected_and_source_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        source = root / "a.txt"
        source.write_text("keep me")
        destination = Path(tmp) / "outside.txt"

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.move", source=str(source), destination=str(destination)))
        assert result.success is False
        assert source.exists()
        assert not destination.exists()


def test_read_only_provider_refuses_every_mutation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", read_only=True, clock=lambda: NOW)
        result = provider.execute(_command("filesystem.create_folder", path=str(root / "new")))
        assert result.success is False
        assert "read-only" in result.detail
        assert not (root / "new").exists()


def test_unknown_service_fails_cleanly_not_an_exception():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.delete_everything"))
        assert result.success is False
        assert "unknown filesystem service" in result.detail


# -- execution: real mutations ----------------------------------------------


def test_create_folder_succeeds_and_verifies():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        target = root / "archive"

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.create_folder", path=str(target)))
        assert result.success is True
        assert target.is_dir()


def test_create_folder_refuses_to_overwrite_an_existing_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        target = root / "archive"
        target.mkdir()

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.create_folder", path=str(target)))
        assert result.success is False
        assert "already exists" in result.detail


def test_copy_leaves_the_source_in_place():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        source = root / "a.txt"
        source.write_text("original")
        destination = root / "b.txt"

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.copy", source=str(source), destination=str(destination)))
        assert result.success is True
        assert source.read_text() == "original"
        assert destination.read_text() == "original"


def test_copy_refuses_to_overwrite_an_existing_destination():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        source = root / "a.txt"
        source.write_text("new")
        destination = root / "b.txt"
        destination.write_text("do not touch me")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.copy", source=str(source), destination=str(destination)))
        assert result.success is False
        assert destination.read_text() == "do not touch me"


def test_move_relocates_the_file_and_source_is_gone():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        source = root / "a.txt"
        source.write_text("payload")
        destination = root / "sub" / "a.txt"
        (root / "sub").mkdir()

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.move", source=str(source), destination=str(destination)))
        assert result.success is True
        assert not source.exists()
        assert destination.read_text() == "payload"


def test_rename_changes_only_the_final_path_component():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        source = root / "draft.txt"
        source.write_text("v1")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.rename", source=str(source), new_name="final.txt"))
        assert result.success is True
        assert not source.exists()
        assert (root / "final.txt").read_text() == "v1"


def test_rename_refuses_to_overwrite_an_existing_sibling():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        source = root / "draft.txt"
        source.write_text("v1")
        (root / "final.txt").write_text("already here")

        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(_command("filesystem.rename", source=str(source), new_name="final.txt"))
        assert result.success is False
        assert source.exists()
        assert (root / "final.txt").read_text() == "already here"


def test_move_of_a_nonexistent_source_fails_cleanly():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        provider = FilesystemProvider(allowed_roots=(root,), scope_id="project:haven", clock=lambda: NOW)
        result = provider.execute(
            _command("filesystem.move", source=str(root / "ghost.txt"), destination=str(root / "elsewhere.txt"))
        )
    assert result.success is False
    assert "does not exist" in result.detail
