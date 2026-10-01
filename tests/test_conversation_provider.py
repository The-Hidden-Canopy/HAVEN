"""Conversation provider contract: bounded, read-only local projection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.integrations.comms import LocalConversationJsonlProvider


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_local_conversation_provider_is_read_only_and_bounds_projection(tmp_path: Path) -> None:
    path = tmp_path / "conversations.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "message_id": "m1",
                        "conversation_id": "c1",
                        "sender": "person@example.invalid",
                        "sent_at": NOW.isoformat(),
                        "body": "secret body " * 100,
                        "labels": ["urgent"],
                    }
                ),
                "not-json",
            ]
        ),
        encoding="utf-8",
    )

    provider = LocalConversationJsonlProvider(path)
    capabilities = provider.capabilities()
    assert capabilities.read is True
    assert capabilities.send is False
    assert capabilities.mutate is False

    (message,) = provider.messages()
    assert message.message_id == "m1"
    assert message.thread_id == "c1"
    assert len(message.snippet) <= 240
    assert "secret body" in message.snippet
    assert not hasattr(message, "body")


def test_local_conversation_provider_rejects_naive_or_invalid_messages(tmp_path: Path) -> None:
    path = tmp_path / "conversations.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"message_id": "naive", "conversation_id": "c", "sender": "s", "sent_at": "2026-10-01T12:00:00"}),
                json.dumps({"message_id": "valid", "conversation_id": "c", "sender": "s", "sent_at": NOW.isoformat()}),
            ]
        ),
        encoding="utf-8",
    )

    assert [message.message_id for message in LocalConversationJsonlProvider(path).messages()] == ["valid"]
    with pytest.raises(ValueError):
        LocalConversationJsonlProvider(path).messages(limit=0)


def test_missing_conversation_file_is_explicitly_unavailable(tmp_path: Path) -> None:
    provider = LocalConversationJsonlProvider(tmp_path / "missing.jsonl")
    assert provider.capabilities().read is False
    assert provider.messages() == ()
