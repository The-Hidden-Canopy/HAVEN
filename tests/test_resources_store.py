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
