"""IPC adapter for browser + comms: honest unavailability, governed tiers,
re-fetch verification, and project attachment over the existing assertion path."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.integrations.browser import BrowserCommandResult, BrowserTabSnapshot
from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:evt-1
SUMMARY:UNLV proposal review
DTSTART:20261002T150000
DTEND:20261002T160000
LOCATION:Room 204
ATTENDEE:mailto:bryan@example.org
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        commands: list[tuple[str, dict]] = []
        instance.browser_hub.connect_browser(
            "chrome",
            ingest=lambda snapshots: instance.browser_provider.ingest_tabs("chrome", snapshots),
            send_command=lambda command, params: commands.append((command, params))
            or BrowserCommandResult(True, "ok"),
        )
        try:
            yield instance, commands
        finally:
            instance.server_close()


def _dispatch(instance, method: str, params: dict) -> dict:
    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def test_browser_full_lifecycle_over_ipc(server) -> None:
    instance, commands = server
    instance.setup.declare_person(name="Gerron Smith", role="owner")

    empty = _dispatch(instance, "browser.tabs.list", {})["result"]
    assert empty["status"]["connected_browsers"] == ["chrome"]
    assert empty["tabs"] == []

    instance.browser_provider.ingest_tabs(
        "chrome",
        (
            BrowserTabSnapshot(tab_id="tab-1", browser="chrome", title="UNLV Proposal - Docs", url="https://docs.google.com/document/d/1", last_active_at=NOW),
            BrowserTabSnapshot(tab_id="tab-9", browser="chrome", title="private", url="https://x.org", last_active_at=NOW, incognito=True),
        ),
    )
    listed = _dispatch(instance, "browser.tabs.list", {})["result"]
    assert [(tab["tab_id"], tab["domain"]) for tab in listed["tabs"]] == [
        ("tab-1", "docs.google.com")
    ]

    focused = _dispatch(instance, "browser.tab.focus", {"resource_id": "browsertab:tab-1"})["result"]
    assert focused["success"] is True
    opened = _dispatch(instance, "browser.tab.open", {"browser": "chrome", "url": "https://haven.example.org"})["result"]
    assert opened["success"] is True
    assert ("open_url", {"url": "https://haven.example.org"}) in commands

    closed = _dispatch(instance, "browser.tab.close", {"resource_id": "browsertab:tab-1"})["result"]
    assert closed["status"] == "confirmation_required"
    denied = _dispatch(instance, "browser.tab.close.deny", {"request_id": closed["request_id"]})["result"]
    assert denied["ok"] is True
    assert instance.resources.get("browsertab:tab-1").stale is False

    ghost = _dispatch(instance, "browser.tab.focus", {"resource_id": "browsertab:ghost"})["result"]
    assert ghost["ok"] is False


def test_calendar_lifecycle_proposal_attachment_and_verified_write(server, tmp_path) -> None:
    instance, _commands = server
    instance.setup.declare_person(name="Gerron Smith", role="owner")
    ics = tmp_path / "cal.ics"
    ics.write_text(ICS, encoding="utf-8")
    added = _dispatch(instance, "calendar.sources.add", {"path": str(ics)})["result"]
    assert added["ok"] is True
    removed = _dispatch(instance, "calendar.sources.remove", {"path": str(ics)})["result"]
    assert str(ics) not in removed["calendar_sources"]
    _dispatch(instance, "calendar.sources.add", {"path": str(ics)})

    events = _dispatch(instance, "calendar.events.list", {})["result"]
    (event,) = events["events"]
    assert event["title"] == "UNLV proposal review"
    assert event["attendees"] == ["bryan@example.org"]
    # Projection into the ResourceStore for search/attachment.
    assert instance.resources.get(event["resource_id"]) is not None

    proposed = _dispatch(instance, "calendar.event.propose_task", {"event_id": "evt-1"})["result"]
    assert proposed["ok"] is True
    assert proposed["task"]["state"] == "proposed"
    assert proposed["task"]["title"] == "Follow up: UNLV proposal review"

    project = _dispatch(instance, "projects.create", {"title": "UNLV Proposal"})["result"]["project"]
    attached = _dispatch(
        instance,
        "calendar.event.attach",
        {"project_id": project["project_id"], "resource_id": event["resource_id"]},
    )["result"]
    assert attached["ok"] is True

    created = _dispatch(
        instance,
        "calendar.event.create",
        {"title": "Follow-up call", "start_at": (NOW + timedelta(days=1)).isoformat()},
    )["result"]
    assert created["status"] == "confirmation_required"
    confirmed = _dispatch(
        instance, "calendar.event.confirm", {"request_id": created["request_id"]}
    )["result"]
    assert confirmed["success"] is True
    assert confirmed["detail"] == "verified by re-read"
    re_read = _dispatch(instance, "calendar.events.list", {})["result"]
    assert any(item["title"] == "Follow-up call" for item in re_read["events"])

    new_event = next(item for item in re_read["events"] if item["title"] == "Follow-up call")
    deleted = _dispatch(instance, "calendar.event.delete", {"event_id": new_event["event_id"]})["result"]
    assert deleted["status"] == "confirmation_required"
    confirmed_delete = _dispatch(
        instance, "calendar.event.confirm", {"request_id": deleted["request_id"]}
    )["result"]
    assert confirmed_delete["success"] is True
    final = _dispatch(instance, "calendar.events.list", {})["result"]
    assert [item["event_id"] for item in final["events"]] == ["evt-1"]


def test_email_unconfigured_then_local_maildir(server, tmp_path) -> None:
    instance, _commands = server
    status = _dispatch(instance, "email.status", {})["result"]
    assert status["configured"] is False
    assert status["capabilities"] == {"read": False, "send": False, "mutate": False}

    messages = _dispatch(instance, "email.messages.list", {})["result"]
    assert messages["ok"] is False
    assert "configured" in messages["error"]

    (tmp_path / "msg-1.eml").write_text(
        "Message-ID: <m1@example.org>\nFrom: ada@example.org\nTo: gerron@example.org\n"
        "Subject: Model results\nDate: Wed, 24 Sep 2026 08:30:00 +0000\n\nBody text here.\n",
        encoding="utf-8",
    )
    _dispatch(instance, "email.maildir.set", {"path": str(tmp_path)})
    status = _dispatch(instance, "email.status", {})["result"]
    assert status["configured"] is True
    assert status["capabilities"]["send"] is False  # read != send, ever

    messages = _dispatch(instance, "email.messages.list", {})["result"]
    assert messages["ok"] is True
    (message,) = messages["messages"]
    assert message["subject"] == "Model results"
    assert message["sender"] == "ada@example.org"
    assert "Body text here" in message["snippet"]
    # Restart durability: the config sidecar persists.
    instance.server_close()
    instance2, _ = make_server(0, data_dir=instance.setup_store.path.parent, clock=lambda: NOW)
    try:
        status = instance2.build_ipc_dispatcher()(
            request_message("r", "email.status", {})
        )["result"]
        assert status["configured"] is True
    finally:
        instance2.server_close()
