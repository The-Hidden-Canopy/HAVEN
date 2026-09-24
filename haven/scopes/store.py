"""`ScopeStore`: SQLite-backed persistence for scopes and memberships.

Same discipline as `haven.resources.store.ResourceStore`: one SQLite file,
a fresh connection per call (no handle outlives a single call -- the
Windows-file-locking reason `HistoryStore`'s docstring gives), and plain
columns for the record fields; JSON only for the open-vocabulary
capability list.

Visibility is *derived*, never caller-supplied: `visible_scope_ids` answers
from the authenticated principal's memberships only. Hierarchy does not
automatically imply inherited visibility -- a principal who is a member of
a child scope does not thereby see the parent, or vice versa; each scope
that should be visible holds its own membership row (design spec pages
18-19).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Membership, ScopeRef

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scopes (
    scope_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_scope_id TEXT,
    status TEXT NOT NULL,
    policy_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    principal_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    role TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    PRIMARY KEY (principal_id, scope_id)
);
CREATE INDEX IF NOT EXISTS memberships_principal ON memberships(principal_id);
CREATE TABLE IF NOT EXISTS legacy_scope_map (
    legacy_scope_id TEXT PRIMARY KEY,
    canonical_scope_id TEXT NOT NULL,
    migrated_at TEXT NOT NULL,
    note TEXT NOT NULL
);
"""


def _to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_str(value: object, *, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


class ScopeStore:
    """SQLite store for `ScopeRef` scopes and `Membership` records."""

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

    # -- scopes ------------------------------------------------------------

    def save_scope(self, scope: ScopeRef) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO scopes(scope_id, kind, name, parent_scope_id, status, policy_ref, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(scope_id) DO UPDATE SET "
                    "kind = excluded.kind, name = excluded.name, "
                    "parent_scope_id = excluded.parent_scope_id, status = excluded.status, "
                    "policy_ref = excluded.policy_ref, created_at = excluded.created_at",
                    (
                        scope.scope_id,
                        scope.kind,
                        scope.name,
                        scope.parent_scope_id,
                        scope.status,
                        scope.policy_ref,
                        _to_str(scope.created_at) or datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_scope(self, scope_id: str) -> ScopeRef | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT scope_id, kind, name, parent_scope_id, status, policy_ref, created_at "
                    "FROM scopes WHERE scope_id = ?",
                    (scope_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return ScopeRef(
            scope_id=row[0],
            kind=row[1],
            name=row[2],
            parent_scope_id=row[3],
            status=row[4],
            policy_ref=row[5],
            created_at=_from_str(row[6], name="scope created_at"),
        )

    def list_scopes(self) -> tuple[ScopeRef, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT scope_id, kind, name, parent_scope_id, status, policy_ref, created_at "
                    "FROM scopes ORDER BY scope_id"
                ).fetchall()
            finally:
                conn.close()
        return tuple(
            ScopeRef(
                scope_id=row[0],
                kind=row[1],
                name=row[2],
                parent_scope_id=row[3],
                status=row[4],
                policy_ref=row[5],
                created_at=_from_str(row[6], name="scope created_at"),
            )
            for row in rows
        )

    # -- memberships ---------------------------------------------------------

    def add_membership(self, membership: Membership) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO memberships(principal_id, scope_id, role, capabilities, valid_from, valid_until) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(principal_id, scope_id) DO UPDATE SET "
                    "role = excluded.role, capabilities = excluded.capabilities, "
                    "valid_from = excluded.valid_from, valid_until = excluded.valid_until",
                    (
                        membership.principal_id,
                        membership.scope_id,
                        membership.role,
                        json.dumps(list(membership.capabilities)),
                        _to_str(membership.valid_from),
                        _to_str(membership.valid_until),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def memberships_for(self, principal_id: str, *, now: datetime | None = None) -> tuple[Membership, ...]:
        """The principal's valid memberships; visibility derives from these."""

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT principal_id, scope_id, role, capabilities, valid_from, valid_until "
                    "FROM memberships WHERE principal_id = ? ORDER BY scope_id",
                    (principal_id,),
                ).fetchall()
            finally:
                conn.close()
        at = now or datetime.now(timezone.utc)
        valid: list[Membership] = []
        for row in rows:
            membership = Membership(
                principal_id=row[0],
                scope_id=row[1],
                role=row[2],
                capabilities=tuple(json.loads(row[3])),
                valid_from=_from_str(row[4], name="membership valid_from"),
                valid_until=_from_str(row[5], name="membership valid_until"),
            )
            if membership.is_valid_at(at):
                valid.append(membership)
        return tuple(valid)

    def visible_scope_ids(self, principal_id: str, *, now: datetime | None = None) -> tuple[str, ...]:
        """Every scope the principal's memberships make visible, sorted.

        Never widened by caller input: the authority boundary calls this and
        then only ever *intersects* with what the caller asked for.
        """

        return tuple(sorted({m.scope_id for m in self.memberships_for(principal_id, now=now)}))

    # -- auditable legacy mapping ---------------------------------------------

    def record_legacy_mapping(
        self, *, legacy_scope_id: str, canonical_scope_id: str, note: str, at: datetime
    ) -> None:
        """Record how a pre-migration scope id maps onto the scope graph.

        Historical receipts and event rows keep their original scope ids;
        this table is what keeps them interpretable after a migration
        moved live data to a canonical scope.
        """

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO legacy_scope_map(legacy_scope_id, canonical_scope_id, migrated_at, note) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(legacy_scope_id) DO UPDATE SET "
                    "canonical_scope_id = excluded.canonical_scope_id, "
                    "migrated_at = excluded.migrated_at, note = excluded.note",
                    (legacy_scope_id, canonical_scope_id, at.isoformat(), note),
                )
                conn.commit()
            finally:
                conn.close()

    def remove_legacy_mapping(self, legacy_scope_id: str) -> None:
        """Drop one mapping row; used by explicit migration rollback."""

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM legacy_scope_map WHERE legacy_scope_id = ?",
                    (legacy_scope_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def legacy_mappings(self) -> tuple[dict, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT legacy_scope_id, canonical_scope_id, migrated_at, note "
                    "FROM legacy_scope_map ORDER BY legacy_scope_id"
                ).fetchall()
            finally:
                conn.close()
        return tuple(
            {
                "legacy_scope_id": row[0],
                "canonical_scope_id": row[1],
                "migrated_at": row[2],
                "note": row[3],
            }
            for row in rows
        )


__all__ = ["ScopeStore"]
