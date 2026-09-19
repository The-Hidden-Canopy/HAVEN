"""Durable knowledge storage for admitted :class:`~haven.knowledge.Claim` values.

The store is deliberately not the admission boundary. Extractors and model
adapters should submit ``CandidateClaim`` values to ``ClaimAdmissionService``;
this class owns the durable representation and indexed provenance lookups that
the admission, search, and knowledge surfaces need.

Every operation opens and closes its own SQLite connection. Besides making the
store safe to use from the web server's worker threads, that is required on
Windows: a live sqlite connection can prevent a chosen data directory from
being moved or removed during tests and backups.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from .claims import Claim, ClaimProvenance, ClaimState, is_stale

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    fingerprint TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_scope ON claims(scope_id);
CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    PRIMARY KEY (claim_id, source_ref)
);
CREATE INDEX IF NOT EXISTS claim_sources_source ON claim_sources(source_ref);
CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    PRIMARY KEY (claim_id, evidence_ref)
);
CREATE INDEX IF NOT EXISTS claim_evidence_ref ON claim_evidence(evidence_ref);
CREATE TABLE IF NOT EXISTS forgotten_claims (
    fingerprint TEXT PRIMARY KEY,
    forgotten_at TEXT NOT NULL,
    forgotten_by TEXT NOT NULL
);
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


def _normalize_proposition(value: str) -> str:
    return " ".join(value.casefold().split())


def fingerprint_for_values(
    *, scope_id: str, proposition: str, provenance: ClaimProvenance, source_refs: Iterable[str]
) -> str:
    """Return the stable dedupe key used by admission and tombstones."""

    parts = (
        scope_id.strip(),
        _normalize_proposition(proposition),
        provenance.value,
        "\x1f".join(sorted({str(ref).strip() for ref in source_refs if str(ref).strip()})),
    )
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def claim_fingerprint(claim: Claim) -> str:
    return fingerprint_for_values(
        scope_id=claim.scope_id,
        proposition=claim.proposition,
        provenance=claim.provenance,
        source_refs=claim.source_refs,
    )


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
        "provenance": claim.provenance.value,
        "supersedes": list(claim.supersedes),
        "contradicts": list(claim.contradicts),
    }


def claim_from_dict(payload: object) -> Claim:
    name = "claim payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("claim_id", "scope_id", "proposition", "state", "created_at"), name=name)
    state = payload["state"]
    if not isinstance(state, str) or state not in ClaimState._value2member_map_:
        raise ValueError(f"unknown claim state: {state!r}")
    provenance = payload.get("provenance", ClaimProvenance.USER_REPORTED.value)
    if not isinstance(provenance, str) or provenance not in ClaimProvenance._value2member_map_:
        raise ValueError(f"unknown claim provenance: {provenance!r}")
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
        provenance=ClaimProvenance(provenance),
        supersedes=tuple(payload.get("supersedes", [])),
        contradicts=tuple(payload.get("contradicts", [])),
    )


class ClaimStore:
    """SQLite-backed upsert store for admitted ``Claim`` values."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(claims)").fetchall()}
            if "fingerprint" not in columns:
                conn.execute("ALTER TABLE claims ADD COLUMN fingerprint TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS claims_fingerprint ON claims(fingerprint)")
            conn.commit()
            self._rebuild_indexes(conn)
            conn.commit()
        finally:
            conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path))

    def _rebuild_indexes(self, conn: sqlite3.Connection) -> None:
        for claim_id, raw in conn.execute("SELECT claim_id, data FROM claims").fetchall():
            try:
                claim = claim_from_dict(json.loads(raw))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            conn.execute("UPDATE claims SET fingerprint = ? WHERE claim_id = ?", (claim_fingerprint(claim), claim_id))
            conn.execute("DELETE FROM claim_sources WHERE claim_id = ?", (claim_id,))
            conn.execute("DELETE FROM claim_evidence WHERE claim_id = ?", (claim_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO claim_sources(claim_id, source_ref) VALUES (?, ?)",
                ((claim_id, ref) for ref in claim.source_refs),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO claim_evidence(claim_id, evidence_ref) VALUES (?, ?)",
                ((claim_id, ref) for ref in claim.evidence_refs),
            )

    def save(self, claim: Claim) -> None:
        fingerprint = claim_fingerprint(claim)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO claims(claim_id, scope_id, fingerprint, data) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(claim_id) DO UPDATE SET scope_id = excluded.scope_id, "
                    "fingerprint = excluded.fingerprint, data = excluded.data",
                    (claim.claim_id, claim.scope_id, fingerprint, json.dumps(claim_to_dict(claim))),
                )
                conn.execute("DELETE FROM claim_sources WHERE claim_id = ?", (claim.claim_id,))
                conn.execute("DELETE FROM claim_evidence WHERE claim_id = ?", (claim.claim_id,))
                conn.executemany(
                    "INSERT INTO claim_sources(claim_id, source_ref) VALUES (?, ?)",
                    ((claim.claim_id, ref) for ref in claim.source_refs),
                )
                conn.executemany(
                    "INSERT INTO claim_evidence(claim_id, evidence_ref) VALUES (?, ?)",
                    ((claim.claim_id, ref) for ref in claim.evidence_refs),
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
        try:
            return claim_from_dict(json.loads(row[0]))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def list_all(self) -> tuple[Claim, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT data FROM claims ORDER BY claim_id").fetchall()
            finally:
                conn.close()
        claims: list[Claim] = []
        for (raw,) in rows:
            try:
                claims.append(claim_from_dict(json.loads(raw)))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return tuple(claims)

    def list_by_scope(self, scope_id: str) -> tuple[Claim, ...]:
        return tuple(claim for claim in self.list_all() if claim.scope_id == scope_id)

    def list_by_state(self, state: ClaimState, *, scope_id: str | None = None) -> tuple[Claim, ...]:
        return tuple(
            claim for claim in self.list_all()
            if claim.state is state and (scope_id is None or claim.scope_id == scope_id)
        )

    def list_by_source(self, source_ref: str) -> tuple[Claim, ...]:
        return tuple(claim for claim in self.list_all() if source_ref in claim.source_refs)

    def list_current_by_scope(self, scope_id: str, *, now: datetime) -> tuple[Claim, ...]:
        return tuple(claim for claim in self.list_by_scope(scope_id) if not is_stale(claim, now=now))

    def find_by_fingerprint(self, fingerprint: str, *, include_stale: bool = False) -> Claim | None:
        for claim in self.list_all():
            if claim_fingerprint(claim) != fingerprint:
                continue
            if not include_stale and claim.state is ClaimState.STALE:
                continue
            return claim
        return None

    def is_forgotten(self, fingerprint: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT 1 FROM forgotten_claims WHERE fingerprint = ?", (fingerprint,)).fetchone()
            finally:
                conn.close()
        return row is not None

    def forget(self, claim: Claim, *, forgotten_at: datetime, forgotten_by: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO forgotten_claims(fingerprint, forgotten_at, forgotten_by) "
                    "VALUES (?, ?, ?)",
                    (claim_fingerprint(claim), _datetime_to_str(forgotten_at), forgotten_by),
                )
                conn.commit()
            finally:
                conn.close()
        self.mark_stale(claim.claim_id)

    def mark_stale(self, claim_id: str) -> bool:
        claim = self.get(claim_id)
        if claim is None or claim.state is ClaimState.STALE:
            return False
        self.save(replace(claim, state=ClaimState.STALE))
        return True

    def mark_stale_by_source(self, source_refs: Iterable[str]) -> int:
        """Stale claims whose complete source set is no longer available."""

        stale_sources = {ref for ref in source_refs if ref}
        marked = 0
        for claim in self.list_all():
            if claim.state is ClaimState.STALE or not claim.source_refs:
                continue
            if set(claim.source_refs).issubset(stale_sources) and self.mark_stale(claim.claim_id):
                marked += 1
        return marked

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

    def count_by_state(self, *, scope_id: str | None = None) -> dict[str, int]:
        counts = {state.value: 0 for state in ClaimState}
        for claim in self.list_all():
            if scope_id is None or claim.scope_id == scope_id:
                counts[claim.state.value] += 1
        return counts


__all__ = [
    "ClaimStore",
    "claim_fingerprint",
    "claim_from_dict",
    "claim_to_dict",
    "fingerprint_for_values",
]
