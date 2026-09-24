"""`ProjectStore`: SQLite persistence for `ProjectRecord`.

House idiom, same as `haven.scopes.store.ScopeStore`: one SQLite file, a
fresh connection per call, JSON blob per row produced by this module's
own codec, and plain columns only for the fields queries filter on
(scope, status). Archive/tombstone is a status, not a deletion: rows are
never removed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ACTIVE, OPEN_STATUSES, ProjectRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_scope ON projects(scope_id);
CREATE INDEX IF NOT EXISTS projects_status ON projects(status);
"""


def _dt(value: datetime) -> str:
    return value.isoformat()


def _parse_dt(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def project_to_dict(record: ProjectRecord) -> dict[str, Any]:
    return {
        "project_id": record.project_id,
        "scope_id": record.scope_id,
        "title": record.title,
        "description": record.description,
        "status": record.status,
        "created_at": _dt(record.created_at),
        "updated_at": _dt(record.updated_at),
        "owner_principal_id": record.owner_principal_id,
        "parent_project_id": record.parent_project_id,
        "source": record.source,
        "revision": record.revision,
        "archived_at": _dt(record.archived_at) if record.archived_at is not None else None,
    }


def project_from_dict(payload: object) -> ProjectRecord:
    name = "project payload"
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a mapping")
    for key in ("project_id", "scope_id", "title", "status", "created_at", "updated_at", "owner_principal_id"):
        if key not in payload:
            raise ValueError(f"{name} is missing key: {key!r}")
    return ProjectRecord(
        project_id=payload["project_id"],
        scope_id=payload["scope_id"],
        title=payload["title"],
        description=str(payload.get("description", "")),
        status=payload["status"],
        created_at=_parse_dt(payload["created_at"], name="created_at"),
        updated_at=_parse_dt(payload["updated_at"], name="updated_at"),
        owner_principal_id=payload["owner_principal_id"],
        parent_project_id=payload.get("parent_project_id"),
        source=str(payload.get("source", "explicit")),
        revision=int(payload.get("revision", 0)),
        archived_at=_parse_dt(payload["archived_at"], name="archived_at") if payload.get("archived_at") else None,
    )


class ProjectStore:
    """SQLite upsert store for projects; archive instead of delete."""

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

    def save(self, record: ProjectRecord) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO projects(project_id, scope_id, status, data) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET "
                    "scope_id = excluded.scope_id, status = excluded.status, data = excluded.data",
                    (record.project_id, record.scope_id, record.status, json.dumps(project_to_dict(record))),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT data FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            finally:
                conn.close()
        return project_from_dict(json.loads(row[0])) if row is not None else None

    def list_by_scope(self, scope_id: str) -> tuple[ProjectRecord, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT data FROM projects WHERE scope_id = ? ORDER BY project_id", (scope_id,)
                ).fetchall()
            finally:
                conn.close()
        return tuple(project_from_dict(json.loads(row[0])) for row in rows)

    def list_visible(self, scope_ids: tuple[str, ...], *, include_archived: bool = False) -> tuple[ProjectRecord, ...]:
        """Every project in the given scopes, archived excluded by default."""

        records: list[ProjectRecord] = []
        for scope_id in scope_ids:
            records.extend(self.list_by_scope(scope_id))
        if not include_archived:
            records = [record for record in records if record.status != "archived"]
        return tuple(sorted(records, key=lambda record: (record.updated_at, record.project_id), reverse=True))


__all__ = ["ProjectStore", "project_from_dict", "project_to_dict"]
