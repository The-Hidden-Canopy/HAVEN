"""`ClaimStore`: SQLite-backed persistence for `Claim`.

Same shape as `haven.resources.store.ResourceStore` /
`haven.web.history_persist.HistoryStore`: one file, a fresh connection per
call, upsert by id. `contradictions_of`/`supersedes_of` resolve a claim's
own `contradicts`/`supersedes` id tuples against what is actually in the
store -- a claim naming an id nothing has been saved under yet (or that was
later removed) simply resolves to fewer results, never an error, the same
"a missing reference degrades, it doesn't crash" discipline
`HistoryStore.load()` already applies to a bad row.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .claims import Claim, ClaimState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_scope ON claims(scope_id);
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


def claim_to_dict(claim: Claim) -> dict:
    return {
        "claim_id": claim.claim_id,
        "scope_id": claim.scope_id,
        "proposition": claim.proposition,
        "state": claim.state.value,
        "source_refs": list(claim.source_refs),
        "evidence_refs": list(claim.evidence_refs),
        "created_at": _datetime_to_str(claim.created_at),
        "valid_until": _datetime_to_str(claim.valid_until) if claim.valid_until is not None else None,
        "confidence": claim.confidence,
        "supersedes": list(claim.supersedes),
        "contradicts": list(claim.contradicts),
    }


def claim_from_dict(payload: object) -> Claim:
    name = "claim payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload, ("claim_id", "scope_id", "proposition", "state", "created_at"), name=name
    )
    state = payload["state"]
    if not isinstance(state, str) or state not in ClaimState._value2member_map_:
        raise ValueError(f"unknown claim state: {state!r}")
    valid_until = payload.get("valid_until")
    return Claim(
        claim_id=payload["claim_id"],
        scope_id=payload["scope_id"],
        proposition=payload["proposition"],
        state=ClaimState(state),
        source_refs=tuple(payload.get("source_refs", [])),
        evidence_refs=tuple(payload.get("evidence_refs", [])),
        created_at=_datetime_from_str(payload["created_at"], name=f"{name} 'created_at'"),
        valid_until=_datetime_from_str(valid_until, name=f"{name} 'valid_until'") if valid_until is not None else None,
        confidence=payload.get("confidence", 1.0),
        supersedes=tuple(payload.get("supersedes", [])),
        contradicts=tuple(payload.get("contradicts", [])),
    )


class ClaimStore:
    """SQLite-backed upsert store for `Claim`, keyed by claim_id."""

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

    def save(self, claim: Claim) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO claims(claim_id, scope_id, data) VALUES (?, ?, ?) "
                    "ON CONFLICT(claim_id) DO UPDATE SET scope_id = excluded.scope_id, data = excluded.data",
                    (claim.claim_id, claim.scope_id, json.dumps(claim_to_dict(claim))),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, claim_id: str) -> Claim | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT data FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return claim_from_dict(json.loads(row[0]))

    def list_by_scope(self, scope_id: str) -> tuple[Claim, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT data FROM claims WHERE scope_id = ? ORDER BY claim_id", (scope_id,)
                ).fetchall()
            finally:
                conn.close()
        claims: list[Claim] = []
        for (raw,) in rows:
            try:
                claims.append(claim_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return tuple(claims)

    def contradictions_of(self, claim_id: str) -> tuple[Claim, ...]:
        claim = self.get(claim_id)
        if claim is None:
            return ()
        return tuple(found for cid in claim.contradicts if (found := self.get(cid)) is not None)

    def supersedes_of(self, claim_id: str) -> tuple[Claim, ...]:
        claim = self.get(claim_id)
        if claim is None:
            return ()
        return tuple(found for cid in claim.supersedes if (found := self.get(cid)) is not None)


__all__ = ["ClaimStore", "claim_from_dict", "claim_to_dict"]
