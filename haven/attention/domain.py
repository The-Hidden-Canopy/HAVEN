"""Needs You domain model (spec section 4).

`AttentionItem` is a protocol-neutral record a source adapter produces from
existing domain state. It carries a typed `RouteRef` and, optionally, typed
`AttentionActionRef`s so the native client can render a button -- but every
button still invokes the existing governed domain method the route names.
The attention layer is a projection and routing surface; it is never a
second authority path (spec 4.1, 23.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..core.time import require_aware_utc


class AttentionKind(str, Enum):
    AUTHORITY = "authority"
    FAILURE = "failure"
    BLOCKER = "blocker"
    AMBIGUITY = "ambiguity"
    CONFLICT = "conflict"
    EXPIRING = "expiring"


class AttentionSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"


class AttentionStatus(str, Enum):
    OPEN = "open"
    SNOOZED = "snoozed"
    RESOLVED = "resolved"
    STALE = "stale"


class Dismissibility(str, Enum):
    NONE = "none"
    SNOOZE = "snooze"
    DISMISS = "dismiss"


# Ranking tiers (spec section 7). Lower sorts first (P0 highest priority).
TIER_CRITICAL = 0
TIER_AUTHORITY = 1
TIER_BLOCKER = 2
TIER_REPAIR = 3
TIER_CONFLICT = 4
TIER_EXPIRING = 5

# Default tier per kind -- a source adapter may override with an explicit
# `severity=CRITICAL` item to earn TIER_CRITICAL regardless of kind (spec
# tier P0 is about consequence, not category).
_KIND_TIER = {
    AttentionKind.AUTHORITY: TIER_AUTHORITY,
    AttentionKind.BLOCKER: TIER_BLOCKER,
    AttentionKind.FAILURE: TIER_REPAIR,
    AttentionKind.CONFLICT: TIER_CONFLICT,
    AttentionKind.AMBIGUITY: TIER_CONFLICT,
    AttentionKind.EXPIRING: TIER_EXPIRING,
}

# Dismissibility policy per kind (spec 5.1). A source adapter may still pass
# a stricter value (e.g. an authority item blocking the active Focus item),
# but never a looser one than its kind allows.
_KIND_DISMISSIBILITY = {
    AttentionKind.AUTHORITY: Dismissibility.NONE,
    AttentionKind.FAILURE: Dismissibility.SNOOZE,
    AttentionKind.BLOCKER: Dismissibility.SNOOZE,
    AttentionKind.AMBIGUITY: Dismissibility.SNOOZE,
    AttentionKind.CONFLICT: Dismissibility.DISMISS,
    AttentionKind.EXPIRING: Dismissibility.DISMISS,
}


def default_dismissibility(kind: AttentionKind) -> Dismissibility:
    return _KIND_DISMISSIBILITY[kind]


def tier_for(kind: AttentionKind, *, severity: AttentionSeverity) -> int:
    if severity is AttentionSeverity.CRITICAL:
        return TIER_CRITICAL
    return _KIND_TIER[kind]


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class RouteRef:
    """Where "Review"/"Open" actually lands (spec section 9)."""

    page: str
    subview: str | None = None
    entity_id: str | None = None
    action_hint: str | None = None
    anchor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "page", _require_text(self.page, name="page"))


@dataclass(frozen=True)
class AttentionActionRef:
    """A typed action the native client may render as a button (spec 4.2).

    `action` names one of: route, review, approve, deny, retry, snooze --
    the client still calls the existing governed domain method; this record
    only tells it which button is safe to show.
    """

    action: str
    label: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _require_text(self.action, name="action"))
        object.__setattr__(self, "label", _require_text(self.label, name="label"))


@dataclass(frozen=True)
class AttentionItem:
    attention_id: str
    kind: AttentionKind
    severity: AttentionSeverity
    title: str
    summary: str
    why_now: str
    source_domain: str
    source_ref: str
    scope_id: str
    created_at: datetime
    route: RouteRef
    due_at: datetime | None = None
    expires_at: datetime | None = None
    stale_after: datetime | None = None
    evidence_refs: tuple[str, ...] = ()
    available_actions: tuple[AttentionActionRef, ...] = ()
    dismissibility: Dismissibility = Dismissibility.NONE
    status: AttentionStatus = AttentionStatus.OPEN
    consequence_summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attention_id", _require_text(self.attention_id, name="attention_id"))
        if not isinstance(self.kind, AttentionKind):
            raise ValueError("kind must be an AttentionKind")
        if not isinstance(self.severity, AttentionSeverity):
            raise ValueError("severity must be an AttentionSeverity")
        object.__setattr__(self, "title", _require_text(self.title, name="title"))
        object.__setattr__(self, "summary", _require_text(self.summary, name="summary"))
        object.__setattr__(self, "why_now", _require_text(self.why_now, name="why_now"))
        object.__setattr__(self, "source_domain", _require_text(self.source_domain, name="source_domain"))
        object.__setattr__(self, "source_ref", _require_text(self.source_ref, name="source_ref"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        if self.due_at is not None:
            object.__setattr__(self, "due_at", require_aware_utc(self.due_at, name="due_at"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at, name="expires_at"))
        if self.stale_after is not None:
            object.__setattr__(self, "stale_after", require_aware_utc(self.stale_after, name="stale_after"))
        if not isinstance(self.route, RouteRef):
            raise ValueError("route must be a RouteRef")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "available_actions", tuple(self.available_actions))
        if not isinstance(self.dismissibility, Dismissibility):
            raise ValueError("dismissibility must be a Dismissibility")
        if not isinstance(self.status, AttentionStatus):
            raise ValueError("status must be an AttentionStatus")

    @property
    def tier(self) -> int:
        return tier_for(self.kind, severity=self.severity)

    def to_dict(self) -> dict:
        return {
            "attention_id": self.attention_id,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "title": self.title,
            "summary": self.summary,
            "why_now": self.why_now,
            "source_domain": self.source_domain,
            "source_ref": self.source_ref,
            "scope_id": self.scope_id,
            "created_at": self.created_at.isoformat(),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "stale_after": self.stale_after.isoformat() if self.stale_after else None,
            "evidence_refs": list(self.evidence_refs),
            "route": {
                "page": self.route.page,
                "subview": self.route.subview,
                "entity_id": self.route.entity_id,
                "action_hint": self.route.action_hint,
                "anchor": self.route.anchor,
            },
            "available_actions": [
                {"action": action.action, "label": action.label} for action in self.available_actions
            ],
            "dismissibility": self.dismissibility.value,
            "status": self.status.value,
            "consequence_summary": self.consequence_summary,
        }


__all__ = [
    "AttentionActionRef",
    "AttentionItem",
    "AttentionKind",
    "AttentionSeverity",
    "AttentionStatus",
    "Dismissibility",
    "RouteRef",
    "default_dismissibility",
    "tier_for",
]
