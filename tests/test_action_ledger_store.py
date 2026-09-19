"""`ActionLedgerStore`: append-only persistence, never an upsert."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.actions import ActionLedgerEntry, ActionLedgerStore
from haven.core.domain import DecisionStatus

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _entry(entry_id="entry-1", *, household_id="haven-1", status=DecisionStatus.ALLOW, **overrides) -> ActionLedgerEntry:
    fields = dict(
        entry_id=entry_id,
        household_id=household_id,
        provider_id="local_filesystem",
        action="filesystem.create_folder",
        resource_id=None,
        requested_by="person:gerron",
        justification="user requested via System > Files",
        parameters=(("path", "C:/Docs/New Folder"),),
        status=status,
        reason="household scope, role, and risk policy are satisfied",
        recorded_at=NOW,
        success=True,
        detail="created folder C:/Docs/New Folder",
    )
    fields.update(overrides)
    return ActionLedgerEntry(**fields)


def test_save_and_get_round_trips_every_field():
    with tempfile.TemporaryDirectory() as tmp:
        store = ActionLedgerStore(Path(tmp) / "action_ledger.db")
        store.save(_entry())
        assert store.get("entry-1") == _entry()


def test_get_missing_entry_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = ActionLedgerStore(Path(tmp) / "action_ledger.db")
        assert store.get("nope") is None


def test_saving_the_same_id_twice_keeps_both_rather_than_upserting():
    with tempfile.TemporaryDirectory() as tmp:
        store = ActionLedgerStore(Path(tmp) / "action_ledger.db")
        store.save(_entry("entry-1"))
        try:
            store.save(_entry("entry-1", detail="a second, conflicting attempt"))
        except Exception:
            pass
        else:
            raise AssertionError("expected a duplicate primary key to fail rather than silently overwrite")


def test_list_by_household_orders_most_recent_first():
    with tempfile.TemporaryDirectory() as tmp:
        store = ActionLedgerStore(Path(tmp) / "action_ledger.db")
        store.save(_entry("entry-1", recorded_at=NOW))
        store.save(_entry("entry-2", recorded_at=datetime(2026, 9, 19, 12, 5, tzinfo=UTC)))

        entries = store.list_by_household("haven-1")
    assert [e.entry_id for e in entries] == ["entry-2", "entry-1"]


def test_list_by_household_never_leaks_another_household():
    with tempfile.TemporaryDirectory() as tmp:
        store = ActionLedgerStore(Path(tmp) / "action_ledger.db")
        store.save(_entry("entry-1", household_id="haven-1"))
        store.save(_entry("entry-2", household_id="haven-2"))

        assert [e.entry_id for e in store.list_by_household("haven-1")] == ["entry-1"]
        assert [e.entry_id for e in store.list_by_household("haven-2")] == ["entry-2"]


def test_a_pending_confirmation_entry_has_no_result_yet():
    with tempfile.TemporaryDirectory() as tmp:
        store = ActionLedgerStore(Path(tmp) / "action_ledger.db")
        store.save(
            _entry(
                "entry-1",
                status=DecisionStatus.CONFIRMATION_REQUIRED,
                reason="this action requires a valid, unexpired confirmation token bound to this request",
                success=None,
                detail=None,
            )
        )
        loaded = store.get("entry-1")
    assert loaded.success is None
    assert loaded.detail is None


def test_persists_across_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "action_ledger.db"
        first = ActionLedgerStore(db_path)
        first.save(_entry())

        second = ActionLedgerStore(db_path)
        assert second.get("entry-1") == _entry()
