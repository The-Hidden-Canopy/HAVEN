"""`ResourceStore`: SQLite persistence, scope isolation, restart survival."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.resources import ResourceRecord, ResourceStore

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _record(
    resource_id="file:proposal-v7", scope_id="project:haven", *, title="proposal-v7.docx"
) -> ResourceRecord:
    return ResourceRecord(
        resource_id=resource_id,
        resource_type="document",
        scope_id=scope_id,
        provider_id="local_computer",
        title=title,
        locator="C:/docs/proposal-v7.docx",
        capabilities=("filesystem.read",),
        observed_at=NOW,
        content_hash="deadbeef",
        metadata=(("author", "gerron"),),
    )


def test_save_and_get_round_trips_every_field():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record())
        loaded = store.get("file:proposal-v7")
    assert loaded == _record()


def test_get_missing_resource_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        assert store.get("file:does-not-exist") is None


def test_saving_the_same_id_again_upserts_not_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record(title="v1"))
        store.save(_record(title="v2"))
        assert store.get("file:proposal-v7").title == "v2"
        assert len(store.list_all()) == 1


def test_scope_isolation_never_leaks_across_scopes():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record("file:a", "project:haven"))
        store.save(_record("file:b", "project:other"))

        haven_only = store.list_by_scope("project:haven")
        assert [r.resource_id for r in haven_only] == ["file:a"]

        other_only = store.list_by_scope("project:other")
        assert [r.resource_id for r in other_only] == ["file:b"]

        assert store.list_by_scope("project:nonexistent") == ()


def test_persists_across_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "resources.db"
        first = ResourceStore(db_path)
        first.save(_record())

        second = ResourceStore(db_path)
        assert second.get("file:proposal-v7") == _record()


def test_a_corrupt_row_is_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "resources.db"
        store = ResourceStore(db_path)
        store.save(_record("file:good", "project:haven"))

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO resources(resource_id, scope_id, resource_type, data) VALUES (?, ?, ?, ?)",
            ("file:bad", "project:haven", "document", "not json"),
        )
        conn.commit()
        conn.close()

        results = store.list_by_scope("project:haven")
    assert [r.resource_id for r in results] == ["file:good"]


def test_new_records_default_to_not_stale():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record())
        assert store.get("file:proposal-v7").stale is False


def test_mark_stale_sets_the_flag_and_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record())
        store.mark_stale("file:proposal-v7")
        assert store.get("file:proposal-v7").stale is True
        # Calling it again on an already-stale record, or on a resource_id
        # that does not exist, must not raise.
        store.mark_stale("file:proposal-v7")
        store.mark_stale("file:does-not-exist")
        assert store.get("file:proposal-v7").stale is True


def test_delete_removes_the_record_outright():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record())
        store.delete("file:proposal-v7")
        assert store.get("file:proposal-v7") is None


def test_mark_stale_by_locator_prefix_only_matches_the_exact_folder():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        root = ResourceRecord(
            resource_id="folder:docs",
            resource_type="folder",
            scope_id="project:haven",
            provider_id="local_computer",
            title="Docs",
            locator="C:/Docs",
            capabilities=("filesystem.read",),
            observed_at=NOW,
        )
        inside = ResourceRecord(
            resource_id="file:inside",
            resource_type="file",
            scope_id="project:haven",
            provider_id="local_computer",
            title="inside.txt",
            locator="C:/Docs/inside.txt",
            capabilities=("filesystem.read",),
            observed_at=NOW,
        )
        sibling = ResourceRecord(
            resource_id="file:sibling",
            resource_type="file",
            scope_id="project:haven",
            provider_id="local_computer",
            title="sibling.txt",
            locator="C:/Docs2/sibling.txt",
            capabilities=("filesystem.read",),
            observed_at=NOW,
        )
        store.save(root)
        store.save(inside)
        store.save(sibling)

        marked = store.mark_stale_by_locator_prefix("C:/Docs")

        # The revoked folder itself and everything under it go stale; a
        # differently-named sibling folder that merely shares the prefix
        # string does not.
        assert marked == 2
        assert store.get("folder:docs").stale is True
        assert store.get("file:inside").stale is True
        assert store.get("file:sibling").stale is False


def test_reconcile_marks_stale_anything_this_scan_did_not_see_again():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        seen_again = _record("file:seen-again", "project:haven")
        deleted = _record("file:deleted-from-disk", "project:haven")
        other_provider = ResourceRecord(
            resource_id="file:other-provider",
            resource_type="file",
            scope_id="project:haven",
            provider_id="home_assistant",
            title="unrelated.txt",
            locator=None,
            capabilities=(),
            observed_at=NOW,
        )
        store.save(seen_again)
        store.save(deleted)
        store.save(other_provider)

        marked = store.reconcile(
            provider_id="local_computer", scope_id="project:haven", observed_ids=["file:seen-again"]
        )

        assert marked == 1
        assert store.get("file:seen-again").stale is False
        assert store.get("file:deleted-from-disk").stale is True
        # A record from a different provider in the same scope is not this
        # scan's business to judge, seen or not.
        assert store.get("file:other-provider").stale is False


def test_reconcile_does_not_touch_a_different_scope():
    with tempfile.TemporaryDirectory() as tmp:
        store = ResourceStore(Path(tmp) / "resources.db")
        store.save(_record("file:elsewhere", "project:other"))

        marked = store.reconcile(provider_id="local_computer", scope_id="project:haven", observed_ids=[])

        assert marked == 0
        assert store.get("file:elsewhere").stale is False
