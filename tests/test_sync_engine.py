"""Sync: allow-set enforcement, two-installation round-trip, conflicts, resolution."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.sync import FolderSyncTransport, LocalSyncEngine, SyncEventStore
from haven.web.server import make_server

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def pair():
    """Two installations with crossed folder transports + personal-scope aliases."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def boot(name: str):
            data = root / name / "data"
            data.mkdir(parents=True)
            server, _ = make_server(0, data_dir=data, clock=lambda: NOW)
            server.setup.declare_person(name="Gerron Smith", role="owner")
            return server

        a, b = boot("a"), boot("b")
        da, db = a.build_ipc_dispatcher(), b.build_ipc_dispatcher()
        for server, dispatcher, out, incoming in (
            (a, da, root / "a-out", root / "b-out"),
            (b, db, root / "b-out", root / "a-out"),
        ):
            dispatcher(
                request_message(
                    "t",
                    "sync.transport.set",
                    {"export_dir": str(out), "import_dir": str(incoming)},
                )
            )
            dispatcher(request_message("e", "sync.set", {"enabled": True}))
        da(request_message("sa", "sync.scope_aliases.set", {
            "aliases": {b.identity.personal_scope_id: a.identity.personal_scope_id}
        }))
        db(request_message("sb", "sync.scope_aliases.set", {
            "aliases": {a.identity.personal_scope_id: b.identity.personal_scope_id}
        }))
        try:
            yield (a, da), (b, db)
        finally:
            for server in (a, b):
                if not getattr(server, "_test_closed", False):
                    server.server_close()


def _call(dispatcher, method: str, params: dict) -> dict:
    return dispatcher(request_message(f"req-{method}", method, params))["result"]


def test_sync_disabled_by_default_and_single_device_functional() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            assert server.sync_engine.enabled is False
            assert server.sync_engine.push() == {"ok": False, "error": "sync is disabled"}
            assert server.sync_engine.pull() == {"ok": False, "error": "sync is disabled"}
            # Producers still work; mutations just are not recorded.
            server.setup.declare_person(name="Gerron Smith", role="owner")
            dispatcher = server.build_ipc_dispatcher()
            created = _call(dispatcher, "projects.create", {"title": "Local only"})
            assert created["ok"] is True
            assert len(server.sync_engine._outbox.since(0)) == 0
        finally:
            server.server_close()


def test_excluded_kinds_never_export_and_never_apply() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = LocalSyncEngine(
            data_dir=Path(tmp) / "data", clock=lambda: NOW, outbox=SyncEventStore(Path(tmp) / "ev.db")
        )
        engine.set_enabled(True)
        refused = engine.record_mutation(
            object_id="cred:ha-token", kind="provider_credential",
            scope_id="scope:personal", revision=0, payload={"token": "secret"},
        )
        assert refused["ok"] is False
        assert "allow-set" in refused["error"]
        assert len(engine._outbox.since(0)) == 0

        # A hand-crafted excluded event in the inbox is rejected on arrival.
        import json
        from haven.sync.events import SyncEvent

        inbox = Path(tmp) / "in"
        inbox.mkdir()
        hostile = SyncEvent(
            event_id="evt-hostile", seq=1, origin_device_id="device:other",
            object_id="cred:x", kind="provider_credential", scope_id="scope:personal",
            revision=0, causal_parents=(), payload=(("token", "secret"),),
            occurred_at=NOW,
        )
        (inbox / "outbox.jsonl").write_text(json.dumps(hostile.wire()) + "\n", encoding="utf-8")
        engine.set_transport(FolderSyncTransport(export_dir=Path(tmp) / "out", import_dir=inbox))
        result = engine.pull()
        assert result["applied"] == 0
        assert result["rejected"] == 1


def test_two_installation_round_trip_converges(pair) -> None:
    (a, da), (b, db) = pair
    project = _call(da, "projects.create", {"title": "Lunar proposal"})["project"]
    task = _call(da, "tasks.create", {
        "title": "Draft sections", "project_id": project["project_id"],
    })["task"]

    pushed = _call(da, "sync.push", {})
    assert pushed["pushed"] == 2
    pulled = _call(db, "sync.pull", {})
    assert pulled["applied"] == 2
    assert _call(db, "projects.get", {"project_id": project["project_id"]})["ok"] is True
    assert _call(db, "tasks.get", {"task_id": task["task_id"]})["ok"] is True

    # B edits; the change flows back and A converges.
    b_task = _call(db, "tasks.get", {"task_id": task["task_id"]})["task"]
    _call(db, "tasks.update", {
        "task_id": task["task_id"], "revision": b_task["revision"], "title": "Draft sections v2",
    })
    _call(db, "sync.push", {})
    pulled_back = _call(da, "sync.pull", {})
    assert pulled_back["applied"] == 1
    assert _call(da, "tasks.get", {"task_id": task["task_id"]})["task"]["title"] == "Draft sections v2"


def test_watermarks_are_restart_durable(pair) -> None:
    (a, da), (b, db) = pair
    task = _call(da, "tasks.create", {"title": "One"})["task"]
    _call(da, "sync.push", {})
    _call(db, "sync.pull", {})

    # Restart B over the same data dir: the inbound watermark survives, so a
    # re-pull does not re-apply (and does not raise spurious conflicts).
    b_data = b.setup_store.path.parent
    b.server_close()
    rebound, _ = make_server(0, data_dir=b_data, clock=lambda: NOW)
    try:
        dispatcher = rebound.build_ipc_dispatcher()
        again = dispatcher(request_message("r", "sync.pull", {}))["result"]
        assert again["pulled"] == 0
        assert again["applied"] == 0
        assert _call(dispatcher, "tasks.get", {"task_id": task["task_id"]})["task"]["title"] == "One"
    finally:
        rebound.server_close()
    b._test_closed = True  # the fixture cleanup must not close it twice


def test_conflict_requires_review_and_resolution(pair) -> None:
    (a, da), (b, db) = pair
    task = _call(da, "tasks.create", {"title": "Base"})["task"]
    _call(da, "sync.push", {})
    _call(db, "sync.pull", {})

    base = _call(db, "tasks.get", {"task_id": task["task_id"]})["task"]
    _call(da, "tasks.update", {"task_id": task["task_id"], "revision": base["revision"], "title": "A title"})
    _call(db, "tasks.update", {"task_id": task["task_id"], "revision": base["revision"], "title": "B title"})
    _call(da, "sync.push", {})
    pulled = _call(db, "sync.pull", {})
    assert len(pulled["conflicts"]) == 1

    conflicts = _call(db, "sync.conflicts", {})["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["local_payload"]["title"] == "B title"
    assert conflicts[0]["remote_payload"]["title"] == "A title"
    # The conflict state blocks neither side's other work, but the record
    # itself stays unresolved until a human chooses.
    assert _call(db, "tasks.get", {"task_id": task["task_id"]})["task"]["title"] == "B title"

    bad = _call(db, "sync.resolve", {"conflict_id": conflicts[0]["conflict_id"], "choice": "both"})
    assert bad["ok"] is False
    unknown_field = _call(db, "sync.resolve", {
        "conflict_id": conflicts[0]["conflict_id"], "choice": "merge",
        "merge_fields": {"not_a_field": "x"},
    })
    assert unknown_field["ok"] is False

    resolved = _call(db, "sync.resolve", {
        "conflict_id": conflicts[0]["conflict_id"], "choice": "remote",
    })
    assert resolved["applied"]["title"] == "A title"
    assert _call(db, "sync.conflicts", {})["conflicts"] == []

    # The resolution itself syncs: A converges on the chosen value.
    _call(db, "sync.push", {})
    _call(da, "sync.pull", {})
    assert _call(da, "tasks.get", {"task_id": task["task_id"]})["task"]["title"] == "A title"


def test_merge_resolution_bumps_revision(pair) -> None:
    (a, da), (b, db) = pair
    task = _call(da, "tasks.create", {"title": "Base"})["task"]
    _call(da, "sync.push", {})
    _call(db, "sync.pull", {})
    base = _call(db, "tasks.get", {"task_id": task["task_id"]})["task"]
    _call(da, "tasks.update", {"task_id": task["task_id"], "revision": base["revision"], "title": "A title"})
    _call(db, "tasks.update", {"task_id": task["task_id"], "revision": base["revision"], "title": "B title"})
    _call(da, "sync.push", {})
    _call(db, "sync.pull", {})
    (conflict,) = _call(db, "sync.conflicts", {})["conflicts"]
    merged = _call(db, "sync.resolve", {
        "conflict_id": conflict["conflict_id"], "choice": "merge",
        "merge_fields": {"title": "Merged title"},
    })
    assert merged["applied"]["title"] == "Merged title"
    assert merged["applied"]["revision"] == base["revision"] + 2  # max + 1
