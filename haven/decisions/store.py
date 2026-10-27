"""Transactional JSON snapshots using HAVEN's SQLite persistence pattern."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from .models import DecisionSnapshot


class DecisionConflict(ValueError):
    """The caller must reload and reconcile a concurrent revision."""


class DecisionStore:
    """One current JSON snapshot per scope; SQLite owns atomicity/recovery.

    A connection never outlives a call. No stale fallback or partial recovery
    may silently resurrect a deleted or superseded decision.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS decision_snapshots (scope_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, data TEXT NOT NULL)")

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA synchronous = FULL")
        return _Connection(conn)

    def load(self, scope_id: str) -> DecisionSnapshot:
        with self._connect() as conn:
            return self._load(conn, scope_id)

    @staticmethod
    def _load(conn, scope_id):
        row = conn.execute("SELECT generation, data FROM decision_snapshots WHERE scope_id = ?", (scope_id,)).fetchone()
        if row is None:
            return DecisionSnapshot(scope_id)
        snapshot = DecisionSnapshot.from_dict(json.loads(row[1]))
        if snapshot.scope_id != scope_id or snapshot.generation != row[0]:
            raise ValueError("decision snapshot metadata mismatch")
        return snapshot

    def update(self, scope_id: str, expected_generation: int,
               transform: Callable[[DecisionSnapshot], DecisionSnapshot], *, before_commit: Callable[[], None]) -> DecisionSnapshot:
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("expected_generation must be a non-negative integer")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._load(conn, scope_id)
            if current.generation != expected_generation:
                raise DecisionConflict("decision snapshot changed; reload before retrying")
            updated = transform(current)
            if updated.scope_id != scope_id or updated.generation != current.generation + 1:
                raise ValueError("invalid snapshot update")
            payload = json.dumps(updated.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(payload.encode("utf-8")) > 8_000_000:
                raise ValueError("decision snapshot exceeds storage budget")
            before_commit()  # fresh identity/scope/consent, including checkpoint resume
            conn.execute("INSERT INTO decision_snapshots VALUES (?, ?, ?) ON CONFLICT(scope_id) DO UPDATE SET generation=excluded.generation, data=excluded.data",
                         (scope_id, updated.generation, payload))
            return updated


class _Connection:
    """sqlite's context manager commits but does not close the handle."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
