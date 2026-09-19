"""`ActionLedgerStore`: a durable, append-only record of every resource
action a household attempted -- denied, needing confirmation, or executed.

Same shape as the other stores in this wave (`ResourceStore`,
`OntologyStore`): one SQLite file, a fresh connection per call, a JSON blob
per row. Unlike those, this store is append-only by design (`save` always
inserts a new row keyed by a fresh `entry_id`, never upserts) -- a ledger
that could silently overwrite its own history would defeat the point of
keeping one. "A ranking pass that cannot explain its own hit is a ranking
pass a household cannot trust" (`haven.search.service`'s own words) applies
just as much to an action that touched a real file: every attempt, allowed
or not, is worth a household being able to look back at.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from haven.core.domain import DecisionStatus
from haven.core.time import require_aware_utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_ledger (
    entry_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS action_ledger_household ON action_ledger(household_id);
"""


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


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
    return require_aware_utc(parsed, name=name)


@dataclass(frozen=True)
class ActionLedgerEntry:
    """One attempt, whatever its outcome. `success`/`detail` are `None`
    until execution actually happens -- a denied or still-pending entry has
    no consequence yet to describe."""

    entry_id: str
    household_id: str
    provider_id: str
    action: str
    resource_id: str | None
    requested_by: str
    justification: str
    parameters: tuple[tuple[str, Any], ...]
    status: DecisionStatus
    reason: str
    recorded_at: datetime
    success: bool | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("entry_id", "household_id", "provider_id", "action", "requested_by", "reason"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        if self.resource_id is not None:
            object.__setattr__(self, "resource_id", _require_text(self.resource_id, name="resource_id"))
        if not isinstance(self.status, DecisionStatus):
            raise ValueError("status must be a DecisionStatus")
        object.__setattr__(self, "parameters", tuple(self.parameters))
        object.__setattr__(self, "recorded_at", require_aware_utc(self.recorded_at, name="recorded_at"))
        if self.detail is not None:
            object.__setattr__(self, "detail", _require_text(self.detail, name="detail"))


def action_ledger_entry_to_dict(entry: ActionLedgerEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "household_id": entry.household_id,
        "provider_id": entry.provider_id,
        "action": entry.action,
        "resource_id": entry.resource_id,
        "requested_by": entry.requested_by,
        "justification": entry.justification,
        "parameters": [[key, value] for key, value in entry.parameters],
        "status": entry.status.value,
        "reason": entry.reason,
        "recorded_at": _datetime_to_str(entry.recorded_at),
        "success": entry.success,
        "detail": entry.detail,
    }


def action_ledger_entry_from_dict(payload: object) -> ActionLedgerEntry:
    name = "action ledger entry payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload,
        ("entry_id", "household_id", "provider_id", "action", "requested_by", "status", "reason", "recorded_at"),
        name=name,
    )
    return ActionLedgerEntry(
        entry_id=payload["entry_id"],
        household_id=payload["household_id"],
        provider_id=payload["provider_id"],
        action=payload["action"],
        resource_id=payload.get("resource_id"),
        requested_by=payload["requested_by"],
        justification=payload.get("justification", ""),
        parameters=tuple((item[0], item[1]) for item in payload.get("parameters", [])),
        status=DecisionStatus(payload["status"]),
        reason=payload["reason"],
        recorded_at=_datetime_from_str(payload["recorded_at"], name=f"{name} 'recorded_at'"),
        success=payload.get("success"),
        detail=payload.get("detail"),
    )


class ActionLedgerStore:
    """Append-only SQLite store for `ActionLedgerEntry`, keyed by entry_id."""

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

    def save(self, entry: ActionLedgerEntry) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO action_ledger(entry_id, household_id, recorded_at, data) VALUES (?, ?, ?, ?)",
                    (
                        entry.entry_id,
                        entry.household_id,
                        _datetime_to_str(entry.recorded_at),
                        json.dumps(action_ledger_entry_to_dict(entry)),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, entry_id: str) -> ActionLedgerEntry | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data FROM action_ledger WHERE entry_id = ?", (entry_id,)
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return action_ledger_entry_from_dict(json.loads(row[0]))

    def list_by_household(self, household_id: str, *, limit: int = 100) -> tuple[ActionLedgerEntry, ...]:
        with self._lock:
            conn = self._connect()
            try:
                # `rowid` breaks ties between entries recorded in the same
                # instant (a fixed test clock, or two attempts within one
                # timestamp's resolution) by actual insertion order, rather
                # than `entry_id`'s random uuid ordering.
                rows = conn.execute(
                    "SELECT data FROM action_ledger WHERE household_id = ? "
                    "ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
                    (household_id, limit),
                ).fetchall()
            finally:
                conn.close()
        entries: list[ActionLedgerEntry] = []
        for (raw,) in rows:
            try:
                entries.append(action_ledger_entry_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return tuple(entries)


__all__ = [
    "ActionLedgerEntry",
    "ActionLedgerStore",
    "action_ledger_entry_from_dict",
    "action_ledger_entry_to_dict",
]
