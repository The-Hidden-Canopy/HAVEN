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
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from ..core.consequence import ConsequenceClass
from ..core.time import require_aware_utc


class AttentionKind(str, Enum):
    AUTHORITY = "authority"
    FAILURE = "failure"
    BLOCKER = "blocker"
    AMBIGUITY = "ambiguity"
    CONFLICT = "conflict"
    EXPIRING = "expiring"
    DEADLINE = "deadline"
    COMMITMENT = "commitment"
    SUGGESTION = "suggestion"
    CONTEXT = "context"


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
    AttentionKind.DEADLINE: TIER_EXPIRING,
    AttentionKind.COMMITMENT: TIER_BLOCKER,
    AttentionKind.SUGGESTION: TIER_CONFLICT,
    AttentionKind.CONTEXT: TIER_CONFLICT,
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
    AttentionKind.DEADLINE: Dismissibility.DISMISS,
    AttentionKind.COMMITMENT: Dismissibility.SNOOZE,
    AttentionKind.SUGGESTION: Dismissibility.DISMISS,
    AttentionKind.CONTEXT: Dismissibility.NONE,
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
    confidence: float | None = None
    consequence_class: ConsequenceClass | None = None

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
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
                raise ValueError("confidence must be a number between 0 and 1")
            if not 0 <= self.confidence <= 1:
                raise ValueError("confidence must be a number between 0 and 1")
        if self.consequence_class is not None and not isinstance(self.consequence_class, ConsequenceClass):
            raise ValueError("consequence_class must be a ConsequenceClass")

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
            "confidence": self.confidence,
            "consequence_class": self.consequence_class.value if self.consequence_class else None,
        }


_PROJECTION_KINDS = {
    "authority": AttentionKind.AUTHORITY,
    "failure": AttentionKind.FAILURE,
    "blocker": AttentionKind.BLOCKER,
    "ambiguity": AttentionKind.AMBIGUITY,
    "conflict": AttentionKind.CONFLICT,
    "expiring": AttentionKind.EXPIRING,
    "deadline": AttentionKind.DEADLINE,
    "commitment": AttentionKind.COMMITMENT,
    "suggestion": AttentionKind.SUGGESTION,
    "context": AttentionKind.CONTEXT,
}


def _projection_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _projection_datetime(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return require_aware_utc(value, name="attention projection time")
    if isinstance(value, str) and value.strip():
        try:
            return require_aware_utc(datetime.fromisoformat(value), name="attention projection time")
        except ValueError:
            pass
    return fallback


def attention_item_from_projection(
    payload: Mapping[str, Any],
    *,
    region: str,
    scope_id: str,
    now: datetime | None = None,
    source_domain: str | None = None,
) -> AttentionItem:
    """Convert a Today-region row into the shared attention-card contract.

    The projection is additive: domain-specific row fields remain available
    to existing clients while the canonical object carries source, reason,
    evidence, actions, confidence, and consequence classification. Missing
    confidence or consequence data stays explicit instead of being invented.
    """

    at = require_aware_utc(now or datetime.now(timezone.utc), name="attention projection now")
    raw_region = _projection_text(region) or "context"
    raw_group = _projection_text(payload.get("group"))
    kind = _PROJECTION_KINDS.get(raw_group or raw_region, AttentionKind.CONTEXT)
    attention_id = (
        _projection_text(payload.get("card_id"))
        or _projection_text(payload.get("attention_id"))
        or _projection_text(payload.get("task_id"))
        or _projection_text(payload.get("resource_id"))
        or _projection_text(payload.get("event_id"))
        or f"{raw_region}:projection"
    )
    source_ref = (
        _projection_text(payload.get("source_ref"))
        or _projection_text(payload.get("task_id"))
        or _projection_text(payload.get("resource_id"))
        or _projection_text(payload.get("event_id"))
        or attention_id
    )
    title = (
        _projection_text(payload.get("title"))
        or _projection_text(payload.get("name"))
        or source_ref
    )
    why_now = (
        _projection_text(payload.get("why_now"))
        or _projection_text(payload.get("summary"))
        or _projection_text(payload.get("detail"))
        or "This item is present in the current Today projection."
    )
    evidence = payload.get("evidence_refs")
    if not isinstance(evidence, (list, tuple)):
        evidence = payload.get("source_refs")
    if not isinstance(evidence, (list, tuple)):
        evidence = [source_ref]
    evidence_refs = tuple(item.strip() for item in evidence if isinstance(item, str) and item.strip()) or (source_ref,)
    next_action = payload.get("next_action")
    if not isinstance(next_action, Mapping):
        next_action = {}
    route_page = _projection_text(next_action.get("route")) or raw_region
    action_label = _projection_text(next_action.get("label"))
    available_actions = (
        (AttentionActionRef(action="route", label=action_label),)
        if action_label
        else ()
    )
    raw_confidence = payload.get("confidence")
    confidence = (
        float(raw_confidence)
        if isinstance(raw_confidence, (int, float))
        and not isinstance(raw_confidence, bool)
        and 0 <= raw_confidence <= 1
        else None
    )
    raw_consequence = _projection_text(payload.get("consequence_class"))
    consequence_class = (
        ConsequenceClass(raw_consequence)
        if raw_consequence in ConsequenceClass._value2member_map_
        else None
    )
    return AttentionItem(
        attention_id=attention_id,
        kind=kind,
        severity=AttentionSeverity.HIGH if kind is AttentionKind.AUTHORITY else AttentionSeverity.NORMAL,
        title=title,
        summary=why_now,
        why_now=why_now,
        source_domain=_projection_text(source_domain) or raw_region,
        source_ref=source_ref,
        scope_id=_projection_text(scope_id) or "unknown",
        created_at=_projection_datetime(
            payload.get("created_at") or payload.get("updated_at") or payload.get("at"),
            fallback=at,
        ),
        due_at=(
            _projection_datetime(payload.get("due_at"), fallback=at)
            if payload.get("due_at") is not None
            else None
        ),
        evidence_refs=evidence_refs,
        route=RouteRef(page=route_page, entity_id=source_ref),
        available_actions=available_actions,
        dismissibility=default_dismissibility(kind),
        confidence=confidence,
        consequence_class=consequence_class,
    )


__all__ = [
    "AttentionActionRef",
    "AttentionItem",
    "AttentionKind",
    "AttentionSeverity",
    "AttentionStatus",
    "Dismissibility",
    "RouteRef",
    "attention_item_from_projection",
    "default_dismissibility",
    "tier_for",
]
