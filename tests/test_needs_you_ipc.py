"""Needs You over IPC: `needs_you.list/.snooze/.dismiss` and the
`today.snapshot` `needs_you` region, exercised through the live server the
same way `test_today_cards.py` exercises `today.*`."""

from __future__ import annotations

import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            yield instance
        finally:
            instance.server_close()


def _dispatch(instance, method: str, params: dict) -> dict:
    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _seed_blocked_task(instance) -> str:
    instance.setup.declare_person(name="Gerron Smith", role="owner")
    dispatcher = instance.build_ipc_dispatcher()
    project = dispatcher(request_message("p", "projects.create", {"title": "VANTA architecture"}))["result"][
        "project"
    ]
    blocker = dispatcher(
        request_message("t1", "tasks.create", {"title": "Choose data model", "project_id": project["project_id"]})
    )["result"]["task"]
    blocked = dispatcher(
        request_message(
            "t2",
            "tasks.create",
            {
                "title": "Implement storage layer",
                "project_id": project["project_id"],
                "dependency_ids": [blocker["task_id"]],
            },
        )
    )["result"]["task"]
    dispatcher(
        request_message(
            "t2-block",
            "tasks.update",
            {"task_id": blocked["task_id"], "revision": blocked["revision"], "state": "blocked"},
        )
    )
    return blocked["task_id"]


def test_no_signals_no_fabricated_items(server) -> None:
    result = _dispatch(server, "needs_you.list", {})["result"]
    assert result == {"ok": True, "count": 0, "items": [], "generated_at": NOW.isoformat()}


def test_blocked_task_surfaces_as_needs_you_blocker(server) -> None:
    _seed_blocked_task(server)
    result = _dispatch(server, "needs_you.list", {})["result"]
    assert result["count"] == 1
    item = result["items"][0]
    assert item["kind"] == "blocker"
    assert item["title"] == "Implement storage layer"
    assert item["why_now"]
    assert item["route"]["page"] == "tasks"
    assert item["dismissibility"] == "snooze"


def test_snooze_hides_item_and_dismiss_is_refused_for_a_blocker(server) -> None:
    _seed_blocked_task(server)
    source_ref = _dispatch(server, "needs_you.list", {})["result"]["items"][0]["source_ref"]

    delivered: list[dict] = []
    unsubscribe = server.events.subscribe(delivered.append)
    try:
        refused = _dispatch(server, "needs_you.dismiss", {"source_ref": source_ref})["result"]
        assert refused["ok"] is False
        assert not any(frame["event"] == "needs_you.changed" for frame in delivered)

        snoozed = _dispatch(server, "needs_you.snooze", {"source_ref": source_ref})["result"]
        assert snoozed["ok"] is True
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(
            frame["event"] == "needs_you.changed" for frame in delivered
        ):
            time.sleep(0.02)
        assert any(frame["event"] == "needs_you.changed" for frame in delivered)
        assert _dispatch(server, "needs_you.list", {})["result"]["count"] == 0
    finally:
        unsubscribe()


def test_snapshot_needs_you_region_matches_needs_you_list(server) -> None:
    _seed_blocked_task(server)
    direct = _dispatch(server, "needs_you.list", {})["result"]
    region = _dispatch(server, "today.snapshot", {})["result"]["snapshot"]["regions"]["needs_you"]
    assert region["count"] == direct["count"] == 1


def test_pending_computer_authority_request_is_not_dismissible(server, tmp_path) -> None:
    allowed = tmp_path / "Documents"
    allowed.mkdir()
    source = allowed / "notes.txt"
    source.write_text("hello")

    dispatcher = server.build_ipc_dispatcher()
    dispatcher(request_message("root", "setup.computer.roots.add", {"path": str(allowed)}))
    dispatcher(request_message("enable", "setup.computer", {"enabled": True, "read_only": False}))
    dispatcher(request_message("owner", "setup.household.people.add", {"name": "Gerron Smith", "role": "owner"}))
    dispatcher(request_message("scan", "setup.computer.scan", {}))

    search = dispatcher(request_message("search", "search.query", {"text": "notes"}))["result"]
    resource_id = search["hits"][0]["resource_id"]

    move = dispatcher(
        request_message(
            "move",
            "computer.action.request",
            {
                "action": "filesystem.move",
                "resource_id": resource_id,
                "parameters": {"source": str(source), "destination": str(allowed / "archive" / "notes.txt")},
                "justification": "tidy up",
            },
        )
    )["result"]
    assert move["status"] == "confirmation_required"

    items = _dispatch(server, "needs_you.list", {})["result"]["items"]
    authority_items = [item for item in items if item["kind"] == "authority"]
    assert authority_items
    assert authority_items[0]["source_domain"] == "computer"
    assert authority_items[0]["dismissibility"] == "none"
    assert _dispatch(server, "needs_you.dismiss", {"source_ref": authority_items[0]["source_ref"]})["result"][
        "ok"
    ] is False
