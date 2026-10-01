"""Conversation provider shape for Slack/Teams-style integrations.

The contract deliberately separates read access from send/mutate authority and
keeps message bodies out of the canonical projection. `LocalConversationJsonlProvider`
is a credential-free fixture/local adapter for development and tests; it does
not claim to be a Slack or Teams connector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ...core.time import require_aware_utc

CONVERSATION_PROVIDER_ID = "haven.conversations.local_jsonl"
SNIPPET_LENGTH = 240


@dataclass(frozen=True)
class ConversationCapabilities:
    read: bool
    send: bool
    mutate: bool
    detail: str


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    conversation_id: str
    thread_id: str
    sender: str
    recipients: tuple[str, ...]
    sent_at: datetime
    snippet: str
    subject: str = ""
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("message_id", "conversation_id", "thread_id", "sender"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "recipients", tuple(self.recipients))
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(self, "sent_at", require_aware_utc(self.sent_at, name="sent_at"))
        object.__setattr__(self, "snippet", " ".join(str(self.snippet).split())[:SNIPPET_LENGTH])
        object.__setattr__(self, "subject", " ".join(str(self.subject).split()))


class ConversationProvider(Protocol):
    provider_id: str

    def capabilities(self) -> ConversationCapabilities: ...

    def messages(self, *, limit: int = 100) -> tuple[ConversationMessage, ...]: ...


class LocalConversationJsonlProvider:
    """Read bounded conversation projections from an explicit JSONL file."""

    provider_id = CONVERSATION_PROVIDER_ID

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def capabilities(self) -> ConversationCapabilities:
        if not self._path.is_file():
            return ConversationCapabilities(
                read=False,
                send=False,
                mutate=False,
                detail=f"the configured conversation file does not exist: {self._path}",
            )
        return ConversationCapabilities(
            read=True,
            send=False,
            mutate=False,
            detail="read-only local conversation projection; external send/mutate needs a governed connector",
        )

    def messages(self, *, limit: int = 100) -> tuple[ConversationMessage, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not self._path.is_file():
            return ()
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        rows: list[ConversationMessage] = []
        for line in lines:
            if len(rows) >= limit:
                break
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    continue
                sent_at = datetime.fromisoformat(str(raw["sent_at"]))
                row = ConversationMessage(
                    message_id=str(raw["message_id"]),
                    conversation_id=str(raw["conversation_id"]),
                    thread_id=str(raw.get("thread_id") or raw["conversation_id"]),
                    sender=str(raw["sender"]),
                    recipients=tuple(str(item) for item in raw.get("recipients", ())),
                    sent_at=sent_at,
                    snippet=str(raw.get("snippet") or raw.get("body") or ""),
                    subject=str(raw.get("subject") or ""),
                    labels=tuple(str(item) for item in raw.get("labels", ())),
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            rows.append(row)
        return tuple(rows)


__all__ = [
    "CONVERSATION_PROVIDER_ID",
    "ConversationCapabilities",
    "ConversationMessage",
    "ConversationProvider",
    "LocalConversationJsonlProvider",
]
