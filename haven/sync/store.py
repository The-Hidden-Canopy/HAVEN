"""The sync outbox: a monotonic, restart-durable event log.

Cursor discipline (spec page 40): the watermark is the last event `seq`
handed to a transport. Each pulled batch advances an inbound watermark
stored per transport, so a restart resumes where the last session stopped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .events import SyncEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    object_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    causal_parents TEXT NOT NULL,
    payload TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sync_events_object ON sync_events(object_id);
"""


class SyncEventStore:
    """SQLite append-only outbox; `seq` is the monotonic watermark."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path))

    def append(self, event: SyncEvent) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sync_events(seq, event_id, object_id, kind, scope_id, "
                    "revision, causal_parents, payload, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.seq if event.seq > 0 else None,
                        event.event_id,
                        event.object_id,
                        event.kind,
                        event.scope_id,
                        event.revision,
                        json.dumps(list(event.causal_parents)),
                        json.dumps(dict(event.payload)),
                        event.occurred_at.isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def since(self, seq: int, *, limit: int = 500) -> tuple[SyncEvent, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT seq, event_id, object_id, kind, scope_id, revision, causal_parents, "
                    "payload, occurred_at FROM sync_events WHERE seq > ? ORDER BY seq LIMIT ?",
                    (seq, limit),
                ).fetchall()
            finally:
                conn.close()
        return tuple(
            SyncEvent(
                event_id=row[1],
                seq=row[0],
                origin_device_id="self",
                object_id=row[2],
                kind=row[3],
                scope_id=row[4],
                revision=row[5],
                causal_parents=tuple(json.loads(row[6])),
                payload=tuple(json.loads(row[7]).items()),
                occurred_at=datetime.fromisoformat(row[8]),
            )
            for row in rows
        )

    def latest_for(self, object_id: str) -> SyncEvent | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT seq, event_id, object_id, kind, scope_id, revision, causal_parents, "
                    "payload, occurred_at FROM sync_events WHERE object_id = ? ORDER BY seq DESC LIMIT 1",
                    (object_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return SyncEvent(
            event_id=row[1],
            seq=row[0],
            origin_device_id="self",
            object_id=row[2],
            kind=row[3],
            scope_id=row[4],
            revision=row[5],
            causal_parents=tuple(json.loads(row[6])),
            payload=tuple(json.loads(row[7]).items()),
            occurred_at=datetime.fromisoformat(row[8]),
        )

    def next_seq(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                (value,) = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM sync_events").fetchone()
            finally:
                conn.close()
        return int(value)


__all__ = ["SyncEventStore"]
