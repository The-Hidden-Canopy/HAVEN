"""Comms: ICS calendar adapter, governed mutations with re-fetch verification,
local .eml email with read/send capability separation, and task proposals."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.integrations.comms import LocalIcsCalendarProvider, LocalMaildirProvider, parse_ics
from haven.integrations.comms.calendar import CalendarEvent

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
ATTENDEE;CN=Ada:mailto:ada@example.org
END:VEVENT
BEGIN:VEVENT
UID:evt-2
SUMMARY:Budget finalization
DTSTART:20261005T090000
END:VEVENT
END:VCALENDAR
"""

EML = """Message-ID: <msg-1@example.org>
From: Bryan <bryan@example.org>
To: gerron@example.org
Subject: UNLV proposal draft
Date: Wed, 24 Sep 2026 08:30:00 +0000
X-Labels: work, unlv

The draft reads well. Two notes inside.
Second line of the message body here.
"""


def test_parse_ics_stdlib() -> None:
    events = parse_ics(ICS, source_path="cal.ics")
    assert [event.event_id for event in events] == ["evt-1", "evt-2"]
    first = events[0]
    assert first.title == "UNLV proposal review"
    assert first.start_at.tzinfo is not None
    assert first.end_at is not None
    assert first.location == "Room 204"
    assert first.attendees == ("bryan@example.org", "ada@example.org")
    assert first.source_path == "cal.ics"


def test_ics_governed_write_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cal.ics"
        path.write_text(ICS, encoding="utf-8")
        provider = LocalIcsCalendarProvider((path,))

        created = provider.create_event(
            CalendarEvent(
                event_id="evt-3",
                title="Follow-up call",
                start_at=NOW + timedelta(days=1),
                location="Phone",
            )
        )
        assert created is not None
        re_read = provider.get("evt-3")
        assert re_read is not None and re_read.title == "Follow-up call"

        updated = provider.update_event(
            CalendarEvent(
                event_id="evt-3",
                title="Follow-up call (moved)",
                start_at=NOW + timedelta(days=2),
                location="Phone",
            )
        )
        assert updated is not None
        assert provider.get("evt-3").title == "Follow-up call (moved)"
        assert len(provider.events()) == 3

        assert provider.delete_event("evt-3") is True
        assert provider.get("evt-3") is None
        assert provider.delete_event("evt-ghost") is False
        # The original events survived the whole cycle.
        assert {event.event_id for event in provider.events()} == {"evt-1", "evt-2"}


def test_local_eml_provider_reads_bounded_snippet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "msg-1.eml").write_text(EML, encoding="utf-8")
        provider = LocalMaildirProvider(folder)

        capabilities = provider.capabilities()
        assert capabilities.read is True
        assert capabilities.send is False and capabilities.mutate is False

        (message,) = provider.messages()
        assert message.sender.startswith("Bryan")
        assert message.subject == "UNLV proposal draft"
        assert message.recipients == ("gerron@example.org",)
        assert message.labels == ("work", "unlv")
        assert "draft reads well" in message.snippet
        assert len(message.snippet) <= 240


def test_unconfigured_and_missing_folder_states_are_explicit() -> None:
    from haven.integrations.comms import UnconfiguredEmailProvider

    unconfigured = UnconfiguredEmailProvider().capabilities()
    assert unconfigured.read is False
    assert "configured" in unconfigured.detail

    with tempfile.TemporaryDirectory() as tmp:
        missing = LocalMaildirProvider(Path(tmp) / "nope").capabilities()
        assert missing.read is False
        assert "does not exist" in missing.detail
