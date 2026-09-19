"""`ResourceStore`: SQLite-backed persistence for `ResourceRecord`.

Same shape as `haven.web.history_persist.HistoryStore` on purpose: one
SQLite file, a fresh connection per call (no handle ever outlives a single
call -- the same Windows-file-locking reason `HistoryStore`'s own docstring
gives), and a JSON blob per row produced by this module's own codec rather
than a summary that cannot rebuild the value. A resource's current state is
upserted, not appended -- a file's title or content hash changing is the
same resource observed again, not a new history entry the way an event is.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .models import ResourceRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS resources_scope ON resources(scope_id);
"""


def _require_mapping(payload: object, *, name: str) -> Mapping:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return payload


def _require_keys(payload: Mapping, keys: tuple[str, ...], *, name: str) -> None:
    for key in keys:
        if key not in payload:
            raise ValueError(f"{name} is missing key: {key!r}")


def _datetime_to_str(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_str(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO datetime string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def resource_record_to_dict(record: ResourceRecord) -> dict:
    return {
        "resource_id": record.resource_id,
        "resource_type": record.resource_type,
        "scope_id": record.scope_id,
        "provider_id": record.provider_id,
        "title": record.title,
        "locator": record.locator,
        "capabilities": list(record.capabilities),
        "observed_at": _datetime_to_str(record.observed_at),
        "content_hash": record.content_hash,
        "metadata": [[key, value] for key, value in record.metadata],
    }


def resource_record_from_dict(payload: object) -> ResourceRecord:
    name = "resource record payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload,
        ("resource_id", "resource_type", "scope_id", "provider_id", "title", "observed_at"),
        name=name,
    )
    metadata_raw = payload.get("metadata", [])
    metadata: list[tuple[str, Any]] = []
    for item in metadata_raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"{name} 'metadata' entries must be [key, value] pairs")
        metadata.append((item[0], item[1]))
    return ResourceRecord(
        resource_id=payload["resource_id"],
        resource_type=payload["resource_type"],
        scope_id=payload["scope_id"],
        provider_id=payload["provider_id"],
        title=payload["title"],
        locator=payload.get("locator"),
        capabilities=tuple(payload.get("capabilities", [])),
        observed_at=_datetime_from_str(payload["observed_at"], name=f"{name} 'observed_at'"),
        content_hash=payload.get("content_hash"),
        metadata=tuple(metadata),
    )


class ResourceStore:
    """SQLite-backed upsert store for `ResourceRecord`, keyed by resource_id."""

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

    def save(self, record: ResourceRecord) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO resources(resource_id, scope_id, resource_type, data) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(resource_id) DO UPDATE SET "
                    "scope_id = excluded.scope_id, resource_type = excluded.resource_type, data = excluded.data",
                    (
                        record.resource_id,
                        record.scope_id,
                        record.resource_type,
                        json.dumps(resource_record_to_dict(record)),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, resource_id: str) -> ResourceRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data FROM resources WHERE resource_id = ?", (resource_id,)
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return resource_record_from_dict(json.loads(row[0]))

    def list_by_scope(self, scope_id: str) -> tuple[ResourceRecord, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT data FROM resources WHERE scope_id = ? ORDER BY resource_id", (scope_id,)
                ).fetchall()
            finally:
                conn.close()
        records: list[ResourceRecord] = []
        for (raw,) in rows:
            try:
                records.append(resource_record_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return tuple(records)

    def list_all(self) -> tuple[ResourceRecord, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT data FROM resources ORDER BY resource_id").fetchall()
            finally:
                conn.close()
        records: list[ResourceRecord] = []
        for (raw,) in rows:
            try:
                records.append(resource_record_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return tuple(records)


__all__ = ["ResourceStore", "resource_record_from_dict", "resource_record_to_dict"]
