"""Durable audit records for user-directed knowledge changes.

Claims describe what HAVEN currently believes.  These records describe who
changed that belief and when.  They are append-only and deliberately live in
the knowledge persistence boundary, separate from device action receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from haven.core.time import require_aware_utc


class KnowledgeAuditAction(str, Enum):
    CORRECT = "correct"
    MARK_STALE = "mark_stale"
    FORGET = "forget"


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class KnowledgeAuditEvent:
    """One append-only, owner-attributed knowledge mutation record."""

    event_id: str
    scope_id: str
    claim_id: str
    action: KnowledgeAuditAction
    actor_id: str
    occurred_at: datetime
    details: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        for field in ("event_id", "scope_id", "claim_id", "actor_id"):
            object.__setattr__(self, field, _require_text(getattr(self, field), name=field))
        if not isinstance(self.action, KnowledgeAuditAction):
            raise ValueError("action must be a KnowledgeAuditAction")
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at, name="occurred_at"))
        object.__setattr__(self, "details", tuple(self.details))


def audit_event_to_dict(event: KnowledgeAuditEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "scope_id": event.scope_id,
        "claim_id": event.claim_id,
        "action": event.action.value,
        "actor_id": event.actor_id,
        "occurred_at": event.occurred_at.isoformat(),
        "details": [[key, value] for key, value in event.details],
    }


def audit_event_from_dict(payload: object) -> KnowledgeAuditEvent:
    if not isinstance(payload, Mapping):
        raise ValueError("knowledge audit payload must be a mapping")
    action = payload.get("action")
    if not isinstance(action, str) or action not in KnowledgeAuditAction._value2member_map_:
        raise ValueError(f"unknown knowledge audit action: {action!r}")
    occurred_at = payload.get("occurred_at")
    if not isinstance(occurred_at, str):
        raise ValueError("knowledge audit 'occurred_at' must be an ISO datetime string")
    try:
        parsed_at = datetime.fromisoformat(occurred_at)
    except ValueError as exc:
        raise ValueError("knowledge audit 'occurred_at' must be an ISO datetime string") from exc
    details_raw = payload.get("details", [])
    if not isinstance(details_raw, list):
        raise ValueError("knowledge audit 'details' must be a list")
    details: list[tuple[str, Any]] = []
    for item in details_raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("knowledge audit detail entries must be [key, value] pairs")
        details.append((_require_text(item[0], name="detail key"), item[1]))
    return KnowledgeAuditEvent(
        event_id=payload.get("event_id"),
        scope_id=payload.get("scope_id"),
        claim_id=payload.get("claim_id"),
        action=KnowledgeAuditAction(action),
        actor_id=payload.get("actor_id"),
        occurred_at=require_aware_utc(parsed_at, name="occurred_at"),
        details=tuple(details),
    )


__all__ = [
    "KnowledgeAuditAction",
    "KnowledgeAuditEvent",
    "audit_event_from_dict",
    "audit_event_to_dict",
]
