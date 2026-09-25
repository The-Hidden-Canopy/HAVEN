"""`ExternalAgentStore`: durable connections, bindings, and the external audit trail.

Same discipline as `haven.scopes.store.ScopeStore`: one SQLite file
(`external_agents.db` in the installation data directory), a fresh
connection per call, plain columns, JSON only for scope lists and bounded
audit detail. Every row is household-scoped.

What this store never holds: raw bearer tokens (only their SHA-256), raw
external subject identifiers (only a hash key plus a display label),
confirmation material, protocol session ids, or provider credentials.
Revocation is a timestamp, never a row deletion, so historical receipts
stay interpretable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import ExternalAgentConnection, ExternalProvider, ExternalScope, PrincipalBinding

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    connection_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    credential_hash TEXT,
    unbound_scopes TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS connections_household ON connections(household_id);
CREATE UNIQUE INDEX IF NOT EXISTS connections_credential ON connections(credential_hash)
    WHERE credential_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS bindings (
    binding_id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    subject_label TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS bindings_subject ON bindings(connection_id, subject_key);
CREATE TABLE IF NOT EXISTS observed_subjects (
    connection_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    subject_label TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, subject_key)
);
CREATE TABLE IF NOT EXISTS audit (
    audit_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    connection_id TEXT,
    actor TEXT,
    detail TEXT NOT NULL,
    security INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_household ON audit(household_id, occurred_at);
CREATE INDEX IF NOT EXISTS audit_connection ON audit(connection_id, occurred_at);
"""

# Audit rows kept per household; older rows are pruned on write. Receipts
# (the durable record of what actually executed) live in history.db and are
# never pruned from here.
AUDIT_RETENTION = 2000


def _to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_str(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamps must be timezone-aware")
    return parsed


def _scopes_to_json(scopes: frozenset[ExternalScope]) -> str:
    return json.dumps(sorted(scope.value for scope in scopes))


def _scopes_from_json(raw: str) -> frozenset[ExternalScope]:
    return frozenset(ExternalScope(value) for value in json.loads(raw))


_CONNECTION_COLUMNS = (
    "connection_id, household_id, provider, display_name, enabled, credential_hash, "
    "unbound_scopes, created_by, created_at, revoked_at"
)
_BINDING_COLUMNS = (
    "binding_id, connection_id, subject_key, subject_label, principal_id, scopes, "
    "created_by, created_at, expires_at, revoked_at"
)


def _connection_from_row(row: tuple) -> ExternalAgentConnection:
    return ExternalAgentConnection(
        connection_id=row[0],
        household_id=row[1],
        provider=ExternalProvider(row[2]),
        display_name=row[3],
        enabled=bool(row[4]),
        credential_hash=row[5],
        unbound_scopes=_scopes_from_json(row[6]),
        created_by=row[7],
        created_at=_from_str(row[8]),
        revoked_at=_from_str(row[9]),
    )


def _binding_from_row(row: tuple) -> PrincipalBinding:
    return PrincipalBinding(
        binding_id=row[0],
        connection_id=row[1],
        subject_key=row[2],
        subject_label=row[3],
        principal_id=row[4],
        scopes=_scopes_from_json(row[5]),
        created_by=row[6],
        created_at=_from_str(row[7]),
        expires_at=_from_str(row[8]),
        revoked_at=_from_str(row[9]),
    )


class ExternalAgentStore:
    """SQLite store for external connections, bindings, observed subjects, and audit."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row[0]) > SCHEMA_VERSION:
                # A newer HAVEN wrote this file: refuse rather than overwrite
                # records this version cannot interpret.
                raise RuntimeError(
                    f"external_agents.db schema {row[0]} is newer than this HAVEN supports ({SCHEMA_VERSION})"
                )
            conn.commit()
        finally:
            conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path))

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    def _read(self, sql: str, params: tuple) -> list[tuple]:
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(sql, params).fetchall()
            finally:
                conn.close()

    # -- connections ---------------------------------------------------------

    def save_connection(self, connection: ExternalAgentConnection) -> None:
        self._write(
            f"INSERT INTO connections({_CONNECTION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(connection_id) DO UPDATE SET "
            "display_name = excluded.display_name, enabled = excluded.enabled, "
            "credential_hash = excluded.credential_hash, unbound_scopes = excluded.unbound_scopes, "
            "revoked_at = excluded.revoked_at",
            (
                connection.connection_id,
                connection.household_id,
                connection.provider.value,
                connection.display_name,
                int(connection.enabled),
                connection.credential_hash,
                _scopes_to_json(connection.unbound_scopes),
                connection.created_by,
                _to_str(connection.created_at),
                _to_str(connection.revoked_at),
            ),
        )

    def get_connection(self, connection_id: str) -> ExternalAgentConnection | None:
        rows = self._read(f"SELECT {_CONNECTION_COLUMNS} FROM connections WHERE connection_id = ?", (connection_id,))
        return _connection_from_row(rows[0]) if rows else None

    def connection_for_credential(self, credential_hash: str) -> ExternalAgentConnection | None:
        rows = self._read(
            f"SELECT {_CONNECTION_COLUMNS} FROM connections WHERE credential_hash = ?", (credential_hash,)
        )
        return _connection_from_row(rows[0]) if rows else None

    def list_connections(self, household_id: str) -> tuple[ExternalAgentConnection, ...]:
        rows = self._read(
            f"SELECT {_CONNECTION_COLUMNS} FROM connections WHERE household_id = ? ORDER BY created_at, connection_id",
            (household_id,),
        )
        return tuple(_connection_from_row(row) for row in rows)

    # -- bindings -------------------------------------------------------------

    def save_binding(self, binding: PrincipalBinding) -> None:
        self._write(
            f"INSERT INTO bindings({_BINDING_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(binding_id) DO UPDATE SET "
            "subject_label = excluded.subject_label, principal_id = excluded.principal_id, "
            "scopes = excluded.scopes, expires_at = excluded.expires_at, revoked_at = excluded.revoked_at",
            (
                binding.binding_id,
                binding.connection_id,
                binding.subject_key,
                binding.subject_label,
                binding.principal_id,
                _scopes_to_json(binding.scopes),
                binding.created_by,
                _to_str(binding.created_at),
                _to_str(binding.expires_at),
                _to_str(binding.revoked_at),
            ),
        )

    def get_binding(self, binding_id: str) -> PrincipalBinding | None:
        rows = self._read(f"SELECT {_BINDING_COLUMNS} FROM bindings WHERE binding_id = ?", (binding_id,))
        return _binding_from_row(rows[0]) if rows else None

    def active_binding(self, connection_id: str, subject_key: str) -> PrincipalBinding | None:
        """The newest unrevoked binding for a subject (expiry is judged by the caller)."""

        rows = self._read(
            f"SELECT {_BINDING_COLUMNS} FROM bindings "
            "WHERE connection_id = ? AND subject_key = ? AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (connection_id, subject_key),
        )
        return _binding_from_row(rows[0]) if rows else None

    def list_bindings(self, connection_id: str, *, include_revoked: bool = False) -> tuple[PrincipalBinding, ...]:
        where = "connection_id = ?" if include_revoked else "connection_id = ? AND revoked_at IS NULL"
        rows = self._read(
            f"SELECT {_BINDING_COLUMNS} FROM bindings WHERE {where} ORDER BY created_at, binding_id",
            (connection_id,),
        )
        return tuple(_binding_from_row(row) for row in rows)

    # -- observed (possibly unbound) subjects -------------------------------------

    def observe_subject(self, connection_id: str, subject_key: str, subject_label: str, at: datetime) -> None:
        """Remember that a subject spoke through a connection, so an owner can bind it later."""

        self._write(
            "INSERT INTO observed_subjects(connection_id, subject_key, subject_label, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(connection_id, subject_key) DO UPDATE SET "
            "subject_label = excluded.subject_label, last_seen_at = excluded.last_seen_at",
            (connection_id, subject_key, subject_label, at.isoformat(), at.isoformat()),
        )

    def observed_subjects(self, connection_id: str) -> tuple[dict[str, str], ...]:
        rows = self._read(
            "SELECT subject_key, subject_label, first_seen_at, last_seen_at FROM observed_subjects "
            "WHERE connection_id = ? ORDER BY last_seen_at DESC",
            (connection_id,),
        )
        return tuple(
            {"subject_key": row[0], "subject_label": row[1], "first_seen_at": row[2], "last_seen_at": row[3]}
            for row in rows
        )

    # -- audit ------------------------------------------------------------------

    def append_audit(
        self,
        *,
        audit_id: str,
        household_id: str,
        kind: str,
        occurred_at: datetime,
        connection_id: str | None = None,
        actor: str | None = None,
        detail: dict[str, Any] | None = None,
        security: bool = False,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO audit(audit_id, household_id, kind, connection_id, actor, detail, security, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        audit_id,
                        household_id,
                        kind,
                        connection_id,
                        actor,
                        json.dumps(detail or {}, sort_keys=True),
                        int(security),
                        occurred_at.isoformat(),
                    ),
                )
                conn.execute(
                    "DELETE FROM audit WHERE household_id = ? AND audit_id NOT IN ("
                    "SELECT audit_id FROM audit WHERE household_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT ?)",
                    (household_id, household_id, AUDIT_RETENTION),
                )
                conn.commit()
            finally:
                conn.close()

    def audit(
        self, household_id: str, *, connection_id: str | None = None, limit: int = 50
    ) -> tuple[dict[str, Any], ...]:
        if connection_id is None:
            sql = "SELECT audit_id, kind, connection_id, actor, detail, security, occurred_at FROM audit WHERE household_id = ?"
            params: tuple = (household_id,)
        else:
            sql = (
                "SELECT audit_id, kind, connection_id, actor, detail, security, occurred_at FROM audit "
                "WHERE household_id = ? AND connection_id = ?"
            )
            params = (household_id, connection_id)
        rows = self._read(sql + " ORDER BY occurred_at DESC, rowid DESC LIMIT ?", params + (limit,))
        return tuple(
            {
                "audit_id": row[0],
                "kind": row[1],
                "connection_id": row[2],
                "actor": row[3],
                "detail": json.loads(row[4]),
                "security": bool(row[5]),
                "occurred_at": row[6],
            }
            for row in rows
        )


__all__ = ["AUDIT_RETENTION", "ExternalAgentStore", "SCHEMA_VERSION"]
