"""Folder transport: the first concrete `SyncProvider` transport (spec page 42).

An export/import directory pair, stdlib only: `send` appends encoded events
to `<export_dir>/outbox.jsonl`; `fetch` reads `<import_dir>/inbox.jsonl`
and returns events past the caller's watermark. Peer-to-peer or encrypted
relay transports implement the same two-method seam later; a folder pair
keeps a single-device install fully functional with sync simply off.
"""

from __future__ import annotations

from pathlib import Path

from .events import SyncEvent, decode_event, encode_event

_OUTBOX_NAME = "outbox.jsonl"


class FolderSyncTransport:
    def __init__(self, *, export_dir: str | Path, import_dir: str | Path) -> None:
        self._export_dir = Path(export_dir)
        self._import_dir = Path(import_dir)

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    @property
    def import_dir(self) -> Path:
        return self._import_dir

    def send(self, events: tuple[SyncEvent, ...]) -> int:
        """Append events to the export outbox; returns how many were written."""

        if not events:
            return 0
        self._export_dir.mkdir(parents=True, exist_ok=True)
        with (self._export_dir / _OUTBOX_NAME).open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(encode_event(event) + "\n")
        return len(events)

    def fetch(self, *, since_seq: int, limit: int = 500) -> tuple[tuple[SyncEvent, ...], int]:
        """Events from the import inbox past `since_seq`, plus the new watermark.

        The watermark here is the remote event's own `seq` -- a folder pair
        is a 1:1 relationship, so remote seq numbers are unambiguous.
        """

        inbox = self._import_dir / _OUTBOX_NAME
        if not inbox.is_file():
            return (), since_seq
        events: list[SyncEvent] = []
        watermark = since_seq
        try:
            lines = inbox.read_text(encoding="utf-8").splitlines()
        except OSError:
            return (), since_seq
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = decode_event(line)
            except (ValueError, KeyError, TypeError):
                continue  # a corrupted line must never stall the stream
            if event.seq <= since_seq:
                continue
            events.append(event)
            watermark = max(watermark, event.seq)
            if len(events) >= limit:
                break
        return tuple(events), watermark


__all__ = ["FolderSyncTransport"]
