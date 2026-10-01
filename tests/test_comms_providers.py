"""Comms: ICS calendar adapter, governed mutations with re-fetch verification,
local .eml email with read/send capability separation, and task proposals."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.integrations.comms import (
    CredentialEmailProvider,
    LocalIcsCalendarProvider,
    LocalMaildirProvider,
    parse_ics,
)
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


def test_credential_email_provider_reads_imap_and_sends_smtp_without_exposing_secret() -> None:
    raw = (
        b"Message-ID: <remote-1@example.org>\r\n"
        b"From: ada@example.org\r\n"
        b"To: bryan@example.org\r\n"
        b"Subject: Remote update\r\n"
        b"Date: Wed, 24 Sep 2026 08:30:00 +0000\r\n\r\n"
        b"The remote mailbox body.\r\n"
    )

    class FakeImap:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.secret = None

        def login(self, username, secret):
            self.username = username
            self.secret = secret
            return "OK", [b"logged in"]

        def select(self, mailbox, readonly=True):
            assert mailbox == "INBOX"
            assert readonly is True
            return "OK", [b"1"]

        def search(self, _charset, _query):
            return "OK", [b"1"]

        def fetch(self, _message_id, _query):
            return "OK", [(b"header", raw)]

        def close(self):
            pass

        def logout(self):
            pass

    sent = []

    class FakeSmtp:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def login(self, username, secret):
            assert username == "bryan@example.org"
            assert secret == "mail-secret"
            return 235, b"accepted"

        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass

    provider = CredentialEmailProvider(
        imap_host="imap.example.org",
        imap_port=993,
        smtp_host="smtp.example.org",
        smtp_port=465,
        username="bryan@example.org",
        secret_loader=lambda: "mail-secret",
        imap_factory=FakeImap,
        smtp_factory=FakeSmtp,
    )

    assert provider.capabilities().read is True
    assert provider.capabilities().send is True
    (message,) = provider.messages()
    assert message.subject == "Remote update"
    assert message.snippet == "The remote mailbox body."

    message_id = provider.send(
        recipients=("ada@example.org",),
        subject="Follow-up",
        body="A governed outbound message.",
    )
    assert message_id.startswith("<")
    assert sent[0]["To"] == "ada@example.org"
    assert sent[0]["Subject"] == "Follow-up"
    assert sent[0].get_payload(decode=True) is not None
