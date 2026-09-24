"""`OntologyStore`: SQLite-backed persistence for `OntologyAssertion`.

Same shape as the other new stores in this wave (`ResourceStore`,
`ClaimStore`). `edges_from`/`edges_to` are the graph-walking primitives a
future resolver would use to answer "what does this project relate to" --
indexed lookups by `subject`/`object`, not a traversal engine; multi-hop
walking (subject -> predicate -> object -> predicate -> object) is a future
resolver's job built on top of these two calls, not something this store
does itself.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .assertions import OntologyAssertion
from ..knowledge.claims import ClaimState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assertions (
    assertion_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assertions_scope ON assertions(scope_id);
CREATE INDEX IF NOT EXISTS assertions_subject ON assertions(subject);
CREATE INDEX IF NOT EXISTS assertions_object ON assertions(object);
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


def assertion_to_dict(assertion: OntologyAssertion) -> dict:
    return {
        "assertion_id": assertion.assertion_id,
        "subject": assertion.subject,
        "predicate": assertion.predicate,
        "object": assertion.object,
        "scope_id": assertion.scope_id,
        "state": assertion.state.value,
        "created_at": _datetime_to_str(assertion.created_at),
        "source_ref": assertion.source_ref,
        "confidence": assertion.confidence,
    }


def assertion_from_dict(payload: object) -> OntologyAssertion:
    name = "ontology assertion payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload,
        ("assertion_id", "subject", "predicate", "object", "scope_id", "state", "created_at"),
        name=name,
    )
    state = payload["state"]
    if not isinstance(state, str) or state not in ClaimState._value2member_map_:
        raise ValueError(f"unknown assertion state: {state!r}")
    return OntologyAssertion(
        assertion_id=payload["assertion_id"],
        subject=payload["subject"],
        predicate=payload["predicate"],
        object=payload["object"],
        scope_id=payload["scope_id"],
        state=ClaimState(state),
        created_at=_datetime_from_str(payload["created_at"], name=f"{name} 'created_at'"),
        source_ref=payload.get("source_ref"),
        confidence=payload.get("confidence", 1.0),
    )


class OntologyStore:
    """SQLite-backed upsert store for `OntologyAssertion`, keyed by assertion_id."""

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

    def save(self, assertion: OntologyAssertion) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO assertions(assertion_id, scope_id, subject, predicate, object, data) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(assertion_id) DO UPDATE SET "
                    "scope_id = excluded.scope_id, subject = excluded.subject, "
                    "predicate = excluded.predicate, object = excluded.object, data = excluded.data",
                    (
                        assertion.assertion_id,
                        assertion.scope_id,
                        assertion.subject,
                        assertion.predicate,
                        assertion.object,
                        json.dumps(assertion_to_dict(assertion)),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, assertion_id: str) -> OntologyAssertion | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data FROM assertions WHERE assertion_id = ?", (assertion_id,)
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return assertion_from_dict(json.loads(row[0]))

    def list_by_scope(self, scope_id: str) -> tuple[OntologyAssertion, ...]:
        return self._query("SELECT data FROM assertions WHERE scope_id = ? ORDER BY assertion_id", (scope_id,))

    def edges_from(
        self, subject: str, *, scope_ids: tuple[str, ...] | None = None
    ) -> tuple[OntologyAssertion, ...]:
        """Every assertion whose `subject` matches -- outgoing edges.

        `scope_ids`, when given, restricts results to assertions in one of
        those scopes -- a relation recorded in one scope must never leak
        into a traversal a caller is running for another. `None` (the
        default) is unrestricted, matching `SearchQuery`'s own "no scope
        filter means every scope the caller can see" contract; closing that
        down to an authority-derived visible-scope set is a future caller's
        job, not this store's.
        """

        return self._query_edges("subject", subject, scope_ids)

    def edges_to(
        self, object_: str, *, scope_ids: tuple[str, ...] | None = None
    ) -> tuple[OntologyAssertion, ...]:
        """Every assertion whose `object` matches -- incoming edges. See
        `edges_from` for `scope_ids`."""

        return self._query_edges("object", object_, scope_ids)

    def _query_edges(
        self, column: str, value: str, scope_ids: tuple[str, ...] | None
    ) -> tuple[OntologyAssertion, ...]:
        if not scope_ids:
            return self._query(f"SELECT data FROM assertions WHERE {column} = ? ORDER BY assertion_id", (value,))
        placeholders = ", ".join("?" for _ in scope_ids)
        return self._query(
            f"SELECT data FROM assertions WHERE {column} = ? AND scope_id IN ({placeholders}) "
            "ORDER BY assertion_id",
            (value, *scope_ids),
        )

    def _query(self, sql: str, params: tuple) -> tuple[OntologyAssertion, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        assertions: list[OntologyAssertion] = []
        for (raw,) in rows:
            try:
                assertions.append(assertion_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return tuple(assertions)

    def remove(self, assertion_id: str) -> bool:
        """Delete one assertion (relationship revocation); True if it existed."""

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM assertions WHERE assertion_id = ?", (assertion_id,)
                )
                conn.commit()
            finally:
                conn.close()
        return cursor.rowcount > 0


__all__ = ["OntologyStore", "assertion_from_dict", "assertion_to_dict"]
