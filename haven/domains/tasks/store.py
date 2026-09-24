"""`TaskStore`: SQLite persistence for `TaskRecord`.

House idiom: fresh connection per call, JSON blob codec, plain columns
for the fields queries filter on (scope, state). Dependents lookup needs
"tasks that depend on X": dependency ids live in the JSON blob, so the
store scans the visible scopes for candidates -- the wake-set only ever
walks a project's connected region anyway, and task counts per scope are
small.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import TaskRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    state TEXT NOT NULL,
    project_id TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_scope ON tasks(scope_id);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project_id);
"""


def _dt(value: datetime) -> str:
    return value.isoformat()


def _parse_dt(value: object, *, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def task_to_dict(record: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "scope_id": record.scope_id,
        "title": record.title,
        "detail": record.detail,
        "state": record.state,
        "created_at": _dt(record.created_at),
        "updated_at": _dt(record.updated_at),
        "created_by": record.created_by,
        "revision": record.revision,
        "project_id": record.project_id,
        "priority": record.priority,
        "due_at": _dt(record.due_at) if record.due_at is not None else None,
        "recurrence": record.recurrence,
        "assignee_person_id": record.assignee_person_id,
        "dependency_ids": list(record.dependency_ids),
        "source_refs": list(record.source_refs),
        "completed_at": _dt(record.completed_at) if record.completed_at is not None else None,
        "completion_evidence_refs": list(record.completion_evidence_refs),
        "completion_evidence_source": record.completion_evidence_source,
    }


def task_from_dict(payload: object) -> TaskRecord:
    name = "task payload"
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a mapping")
    for key in ("task_id", "scope_id", "title", "state", "created_at", "updated_at", "created_by"):
        if key not in payload:
            raise ValueError(f"{name} is missing key: {key!r}")
    return TaskRecord(
        task_id=payload["task_id"],
        scope_id=payload["scope_id"],
        title=payload["title"],
        detail=str(payload.get("detail", "")),
        state=payload["state"],
        created_at=_parse_dt(payload["created_at"], name="created_at"),
        updated_at=_parse_dt(payload["updated_at"], name="updated_at"),
        created_by=payload["created_by"],
        revision=int(payload.get("revision", 0)),
        project_id=payload.get("project_id"),
        priority=payload.get("priority"),
        due_at=_parse_dt(payload.get("due_at"), name="due_at"),
        recurrence=payload.get("recurrence"),
        assignee_person_id=payload.get("assignee_person_id"),
        dependency_ids=tuple(payload.get("dependency_ids", ())),
        source_refs=tuple(payload.get("source_refs", ())),
        completed_at=_parse_dt(payload.get("completed_at"), name="completed_at"),
        completion_evidence_refs=tuple(payload.get("completion_evidence_refs", ())),
        completion_evidence_source=payload.get("completion_evidence_source"),
    )


class TaskStore:
    """SQLite upsert store for tasks."""

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

    def save(self, record: TaskRecord) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO tasks(task_id, scope_id, state, project_id, data) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(task_id) DO UPDATE SET "
                    "scope_id = excluded.scope_id, state = excluded.state, "
                    "project_id = excluded.project_id, data = excluded.data",
                    (
                        record.task_id,
                        record.scope_id,
                        record.state,
                        record.project_id,
                        json.dumps(task_to_dict(record)),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT data FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            finally:
                conn.close()
        return task_from_dict(json.loads(row[0])) if row is not None else None

    def list_by_scope(self, scope_id: str) -> tuple[TaskRecord, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT data FROM tasks WHERE scope_id = ? ORDER BY task_id", (scope_id,)
                ).fetchall()
            finally:
                conn.close()
        return tuple(task_from_dict(json.loads(row[0])) for row in rows)

    def list_visible(self, scope_ids: tuple[str, ...]) -> tuple[TaskRecord, ...]:
        records: list[TaskRecord] = []
        for scope_id in scope_ids:
            records.extend(self.list_by_scope(scope_id))
        return tuple(sorted(records, key=lambda record: (record.updated_at, record.task_id), reverse=True))

    def dependents_of(self, task_id: str, *, scope_ids: tuple[str, ...]) -> tuple[TaskRecord, ...]:
        """Every visible task that lists ``task_id`` among its dependencies."""

        return tuple(
            record
            for record in self.list_visible(scope_ids)
            if task_id in record.dependency_ids
        )


__all__ = ["TaskStore", "task_from_dict", "task_to_dict"]
