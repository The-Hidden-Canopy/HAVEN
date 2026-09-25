"""Durable history: events, actions, receipts, and memory across a restart.

The core store is transition-only and in-memory (`haven/core/store.py`), so
without this module the entire audit trail -- what was authorized, what
executed, what a household's receipts and memory say -- lives and dies with
the process, even though rules (`rules_persist.py`) already survive a
restart. `HistoryStore` is the same idea applied to the household's history:
one SQLite file, one table per kind, each row an opaque JSON blob produced
by this module's own codecs -- the same "a payload that cannot rebuild the
value is not persistence" idiom `rules_persist.py` already uses, just backed
by SQLite instead of one JSON file, because this data grows without bound
over a household's lifetime the way `rules.json` never does.

Confirmation tokens are never persisted: `action_request_to_dict` drops
`ActionRequest.confirmation_token` entirely, the same discipline
`ActionReceipt.to_dict()` already keeps for its own JSON export. A single-use
security credential has no business surviving into a history row, and
`AuthorityEngine`'s own consumed-token tracking is what actually prevents
reuse regardless -- this is defense in depth, not the enforcement mechanism.
One consequence: `confirmation_present` on a restored `ActionRequest` always
reads `False`, even for an action that really was confirmed before the
restart. That is an accepted, deliberate loss of one historical display
flag, not a governance gap -- the action already executed correctly under
the original process's authorization.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from haven.audit.receipts import ActionReceipt
from haven.core.domain import (
    ActionKind,
    ActionOrigin,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    AuthorityDecision,
    DecisionCode,
    DecisionStatus,
    DeviceResult,
    DomainEvent,
    EventType,
    EvidenceRef,
    EvidenceStatus,
    MemoryEntry,
    RoleTier,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_household ON events(household_id, occurred_at);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_household ON actions(household_id);

CREATE TABLE IF NOT EXISTS memory (
    entry_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_household ON memory(household_id, recorded_at);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_household ON receipts(household_id);
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


def _parameters_to_list(parameters: tuple[tuple[str, Any], ...]) -> list[list[Any]]:
    return [[key, value] for key, value in parameters]


def _parameters_from_list(payload: object, *, name: str) -> tuple[tuple[str, Any], ...]:
    if not isinstance(payload, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    pairs: list[tuple[str, Any]] = []
    for item in payload:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"{name} entries must be [key, value] pairs")
        key, value = item
        if not isinstance(key, str):
            raise ValueError(f"{name} keys must be strings")
        pairs.append((key, value))
    return tuple(pairs)


def action_request_to_dict(request: ActionRequest) -> dict:
    return {
        "request_id": request.request_id,
        "household_id": request.household_id,
        "requested_by": request.requested_by,
        "rule_id": request.rule_id,
        "action_kind": request.action_kind.value,
        "target_device_id": request.target_device_id,
        "parameters": _parameters_to_list(request.parameters),
        "justification": request.justification,
        "evidence_snapshot_id": request.evidence_snapshot_id,
        "requested_at": _datetime_to_str(request.requested_at),
        "origin": request.origin.value,
        "capability": request.capability,
    }


def action_request_from_dict(payload: object) -> ActionRequest:
    name = "action request payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload,
        (
            "request_id",
            "household_id",
            "requested_by",
            "rule_id",
            "action_kind",
            "target_device_id",
            "justification",
            "evidence_snapshot_id",
            "requested_at",
        ),
        name=name,
    )
    action_kind = payload["action_kind"]
    if not isinstance(action_kind, str) or action_kind not in ActionKind._value2member_map_:
        raise ValueError(f"unknown action_kind: {action_kind!r}")
    origin = payload.get("origin", ActionOrigin.RULE.value)
    if not isinstance(origin, str) or origin not in ActionOrigin._value2member_map_:
        raise ValueError(f"unknown action origin: {origin!r}")
    return ActionRequest(
        request_id=payload["request_id"],
        household_id=payload["household_id"],
        requested_by=payload["requested_by"],
        rule_id=payload["rule_id"],
        action_kind=ActionKind(action_kind),
        target_device_id=payload["target_device_id"],
        parameters=_parameters_from_list(payload.get("parameters", []), name=f"{name} 'parameters'"),
        justification=payload["justification"],
        evidence_snapshot_id=payload["evidence_snapshot_id"],
        requested_at=_datetime_from_str(payload["requested_at"], name=f"{name} 'requested_at'"),
        origin=ActionOrigin(origin),
        # Never restored -- see module docstring.
        confirmation_token=None,
        capability=payload.get("capability"),
    )


def authority_decision_to_dict(decision: AuthorityDecision) -> dict:
    return {
        "status": decision.status.value,
        "code": decision.code.value,
        "explanation": decision.explanation,
        "required_role": decision.required_role.value if decision.required_role is not None else None,
    }


def authority_decision_from_dict(payload: object) -> AuthorityDecision:
    name = "authority decision payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("status", "code", "explanation"), name=name)
    status = payload["status"]
    if not isinstance(status, str) or status not in DecisionStatus._value2member_map_:
        raise ValueError(f"unknown decision status: {status!r}")
    code = payload["code"]
    if not isinstance(code, str) or code not in DecisionCode._value2member_map_:
        raise ValueError(f"unknown decision code: {code!r}")
    required_role = payload.get("required_role")
    if required_role is not None:
        try:
            required_role = RoleTier(required_role)
        except ValueError as exc:
            raise ValueError(f"unknown required_role: {required_role!r}") from exc
    return AuthorityDecision(
        status=DecisionStatus(status),
        code=DecisionCode(code),
        explanation=payload["explanation"],
        required_role=required_role,
    )


def device_result_to_dict(result: DeviceResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "success": result.success,
        "detail": result.detail,
        "observed_at": _datetime_to_str(result.observed_at),
        "source": result.source,
    }


def device_result_from_dict(payload: object) -> DeviceResult | None:
    if payload is None:
        return None
    name = "device result payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("success", "detail", "observed_at", "source"), name=name)
    return DeviceResult(
        success=bool(payload["success"]),
        detail=payload["detail"],
        observed_at=_datetime_from_str(payload["observed_at"], name=f"{name} 'observed_at'"),
        source=payload["source"],
    )


def action_record_to_dict(action: ActionRecord) -> dict:
    return {
        "action_id": action.action_id,
        "request": action_request_to_dict(action.request),
        "status": action.status.value,
        "decision": authority_decision_to_dict(action.decision),
        "executed_at": _datetime_to_str(action.executed_at) if action.executed_at is not None else None,
        "result": device_result_to_dict(action.result),
    }


def action_record_from_dict(payload: object) -> ActionRecord:
    name = "action record payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("action_id", "request", "status", "decision"), name=name)
    status = payload["status"]
    if not isinstance(status, str) or status not in ActionStatus._value2member_map_:
        raise ValueError(f"unknown action status: {status!r}")
    executed_at = payload.get("executed_at")
    return ActionRecord(
        action_id=payload["action_id"],
        request=action_request_from_dict(payload["request"]),
        status=ActionStatus(status),
        decision=authority_decision_from_dict(payload["decision"]),
        executed_at=_datetime_from_str(executed_at, name=f"{name} 'executed_at'") if executed_at is not None else None,
        result=device_result_from_dict(payload.get("result")),
    )


def domain_event_to_dict(event: DomainEvent) -> dict:
    return {
        "event_id": event.event_id,
        "household_id": event.household_id,
        "event_type": event.event_type.value,
        "actor_id": event.actor_id,
        "occurred_at": _datetime_to_str(event.occurred_at),
        "payload": _parameters_to_list(event.payload),
        "correlation_id": event.correlation_id,
        "source": event.source,
    }


def domain_event_from_dict(payload: object) -> DomainEvent:
    name = "domain event payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload,
        ("event_id", "household_id", "event_type", "actor_id", "occurred_at", "correlation_id", "source"),
        name=name,
    )
    event_type = payload["event_type"]
    if not isinstance(event_type, str) or event_type not in EventType._value2member_map_:
        raise ValueError(f"unknown event_type: {event_type!r}")
    return DomainEvent(
        event_id=payload["event_id"],
        household_id=payload["household_id"],
        event_type=EventType(event_type),
        actor_id=payload["actor_id"],
        occurred_at=_datetime_from_str(payload["occurred_at"], name=f"{name} 'occurred_at'"),
        payload=_parameters_from_list(payload.get("payload", []), name=f"{name} 'payload'"),
        correlation_id=payload["correlation_id"],
        source=payload["source"],
    )


def memory_entry_to_dict(entry: MemoryEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "household_id": entry.household_id,
        "kind": entry.kind,
        "content": entry.content,
        "source_event_id": entry.source_event_id,
        "recorded_at": _datetime_to_str(entry.recorded_at),
    }


def memory_entry_from_dict(payload: object) -> MemoryEntry:
    name = "memory entry payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("entry_id", "household_id", "kind", "content", "source_event_id", "recorded_at"), name=name)
    return MemoryEntry(
        entry_id=payload["entry_id"],
        household_id=payload["household_id"],
        kind=payload["kind"],
        content=payload["content"],
        source_event_id=payload["source_event_id"],
        recorded_at=_datetime_from_str(payload["recorded_at"], name=f"{name} 'recorded_at'"),
    )


def evidence_ref_to_dict(ref: EvidenceRef) -> dict:
    return {
        "kind": ref.kind,
        "subject_id": ref.subject_id,
        "status": ref.status.value,
        "observed_at": _datetime_to_str(ref.observed_at),
        "source": ref.source,
    }


def evidence_ref_from_dict(payload: object) -> EvidenceRef:
    name = "evidence ref payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("kind", "subject_id", "status", "observed_at", "source"), name=name)
    status = payload["status"]
    if not isinstance(status, str) or status not in EvidenceStatus._value2member_map_:
        raise ValueError(f"unknown evidence status: {status!r}")
    return EvidenceRef(
        kind=payload["kind"],
        subject_id=payload["subject_id"],
        status=EvidenceStatus(status),
        observed_at=_datetime_from_str(payload["observed_at"], name=f"{name} 'observed_at'"),
        source=payload["source"],
    )


def receipt_to_dict(receipt: ActionReceipt) -> dict:
    """Full-fidelity persistence codec -- deliberately separate from
    `ActionReceipt.to_dict()`, which is a lossy, display-oriented export."""

    payload = {
        "receipt_id": receipt.receipt_id,
        "requested_action": action_request_to_dict(receipt.requested_action),
        "interpretation": receipt.interpretation,
        "evidence": [evidence_ref_to_dict(ref) for ref in receipt.evidence],
        "decision": authority_decision_to_dict(receipt.decision),
        "device_result": device_result_to_dict(receipt.device_result),
        "event_ids": list(receipt.event_ids),
    }
    if receipt.external_source is not None:
        payload["external_source"] = dict(receipt.external_source)
    return payload


def receipt_from_dict(payload: object) -> ActionReceipt:
    name = "receipt payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(payload, ("receipt_id", "requested_action", "interpretation", "decision"), name=name)
    return ActionReceipt(
        receipt_id=payload["receipt_id"],
        requested_action=action_request_from_dict(payload["requested_action"]),
        interpretation=payload["interpretation"],
        evidence=tuple(evidence_ref_from_dict(item) for item in payload.get("evidence", [])),
        decision=authority_decision_from_dict(payload["decision"]),
        device_result=device_result_from_dict(payload.get("device_result")),
        event_ids=tuple(payload.get("event_ids", [])),
        external_source=_external_source_from(payload.get("external_source")),
    )


def _external_source_from(value: object) -> tuple[tuple[str, str], ...] | None:
    if value is None:
        return None
    mapping = _require_mapping(value, name="receipt 'external_source'")
    return tuple((str(key), str(item)) for key, item in mapping.items())


@dataclass(frozen=True)
class HistorySnapshot:
    """One household's durable history, loaded at boot."""

    events: tuple[DomainEvent, ...] = ()
    actions: tuple[ActionRecord, ...] = ()
    memory: tuple[MemoryEntry, ...] = ()
    receipts: tuple[ActionReceipt, ...] = ()


class HistoryStore:
    """SQLite-backed append log for events, actions, memory, and receipts.

    Each call opens its own connection, does its work, and closes it --
    deliberately, not for performance (this data is written at most a few
    times per user action, never in a hot loop) but so no open file handle
    ever outlives a single call. A `DemoDirector` that never explicitly
    closes this (nothing in this module forces it to) must not leave
    `history.db` locked for the life of the process, and this is the
    simplest way to guarantee that on every platform, Windows included,
    where an open sqlite3 connection blocks the file's deletion.

    Writes are idempotent (`INSERT OR IGNORE` for the append-only tables,
    upsert-by-id for `actions`, since `HavenStore` replaces an action's row
    in place as it moves AUTHORIZED -> EXECUTED), so a caller can persist
    the same row more than once -- e.g. after every `_publish_state()` --
    without corrupting anything or raising. One bad row on `load()` is
    skipped rather than allowed to eat the household's other history, the
    same discipline `RulesPersistence.load()` already keeps.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # `with conn:` on a sqlite3.Connection only manages the transaction
        # (commit/rollback) -- it does NOT close the connection, so this
        # must close explicitly like every other method here.
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

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    def append_event(self, event: DomainEvent) -> None:
        self._write(
            "INSERT OR IGNORE INTO events(event_id, household_id, occurred_at, data) VALUES (?, ?, ?, ?)",
            (event.event_id, event.household_id, event.occurred_at.isoformat(), json.dumps(domain_event_to_dict(event))),
        )

    def save_action(self, action: ActionRecord) -> None:
        self._write(
            "INSERT INTO actions(action_id, household_id, data) VALUES (?, ?, ?) "
            "ON CONFLICT(action_id) DO UPDATE SET data = excluded.data",
            (action.action_id, action.request.household_id, json.dumps(action_record_to_dict(action))),
        )

    def append_memory(self, entry: MemoryEntry) -> None:
        self._write(
            "INSERT OR IGNORE INTO memory(entry_id, household_id, recorded_at, data) VALUES (?, ?, ?, ?)",
            (entry.entry_id, entry.household_id, entry.recorded_at.isoformat(), json.dumps(memory_entry_to_dict(entry))),
        )

    def append_receipt(self, receipt: ActionReceipt) -> None:
        self._write(
            "INSERT OR IGNORE INTO receipts(receipt_id, household_id, data) VALUES (?, ?, ?)",
            (receipt.receipt_id, receipt.requested_action.household_id, json.dumps(receipt_to_dict(receipt))),
        )

    def load(self, household_id: str) -> HistorySnapshot:
        with self._lock:
            conn = self._connect()
            try:
                event_rows = conn.execute(
                    "SELECT data FROM events WHERE household_id = ? ORDER BY occurred_at", (household_id,)
                ).fetchall()
                action_rows = conn.execute(
                    "SELECT data FROM actions WHERE household_id = ?", (household_id,)
                ).fetchall()
                memory_rows = conn.execute(
                    "SELECT data FROM memory WHERE household_id = ? ORDER BY recorded_at", (household_id,)
                ).fetchall()
                receipt_rows = conn.execute(
                    "SELECT data FROM receipts WHERE household_id = ?", (household_id,)
                ).fetchall()
            finally:
                conn.close()

        events: list[DomainEvent] = []
        for (raw,) in event_rows:
            try:
                events.append(domain_event_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        actions: list[ActionRecord] = []
        for (raw,) in action_rows:
            try:
                actions.append(action_record_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        memory: list[MemoryEntry] = []
        for (raw,) in memory_rows:
            try:
                memory.append(memory_entry_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        receipts: list[ActionReceipt] = []
        for (raw,) in receipt_rows:
            try:
                receipts.append(receipt_from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return HistorySnapshot(
            events=tuple(events), actions=tuple(actions), memory=tuple(memory), receipts=tuple(receipts)
        )

    def close(self) -> None:
        """No-op: every operation already opens and closes its own connection.

        Kept so callers (`DemoDirector.close_history`) have a stable
        lifecycle method to call regardless of how this class is implemented
        underneath.
        """


__all__ = [
    "HistorySnapshot",
    "HistoryStore",
    "action_record_from_dict",
    "action_record_to_dict",
    "action_request_from_dict",
    "action_request_to_dict",
    "authority_decision_from_dict",
    "authority_decision_to_dict",
    "device_result_from_dict",
    "device_result_to_dict",
    "domain_event_from_dict",
    "domain_event_to_dict",
    "evidence_ref_from_dict",
    "evidence_ref_to_dict",
    "memory_entry_from_dict",
    "memory_entry_to_dict",
    "receipt_from_dict",
    "receipt_to_dict",
]
