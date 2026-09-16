"""Alert domain values: what an alert is, and the deterministic conditions
that must all hold before one fires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from haven.core.domain import EvidenceRef, PredictionTrigger
from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_confidence(value: float, *, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a number between 0.0 and 1.0")


class AlertSeverity(str, Enum):
    """Ascending order matches the household's own escalation ladder."""

    INFO = "info"
    NOTICE = "notice"
    IMPORTANT = "important"
    URGENT = "urgent"
    CRITICAL = "critical"


@dataclass(frozen=True)
class PresenceCondition:
    """An AlertRule condition: this person must (not) be present in this room."""

    person_id: str
    room_id: str
    present: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "person_id", _require_text(self.person_id, name="person_id"))
        object.__setattr__(self, "room_id", _require_text(self.room_id, name="room_id"))


@dataclass(frozen=True)
class ContextCondition:
    """An AlertRule condition: this household context must (not) be active."""

    context_id: str
    active: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _require_text(self.context_id, name="context_id"))


@dataclass(frozen=True)
class AlertRule:
    """Deterministic policy for when one alert fires.

    Every declared condition -- each `presence_conditions` entry, each
    `context_conditions` entry, and `prediction_trigger` if set -- must hold
    before `AlertEngine.evaluate()` returns an `Alert`. This is what turns
    "person detected" into "person at front door AND nobody expected AND
    household away": the correlation is the point, not any single detector.
    An `AlertRule` declaring no conditions at all would fire unconditionally
    on every evaluation, so it is rejected at construction.
    """

    alert_id: str
    household_id: str
    event_kind: str
    severity: AlertSeverity
    summary: str
    presence_conditions: tuple[PresenceCondition, ...] = ()
    context_conditions: tuple[ContextCondition, ...] = ()
    prediction_trigger: PredictionTrigger | None = None
    minimum_confidence: float = 1.0

    def __post_init__(self) -> None:
        for field_name in ("alert_id", "household_id", "event_kind", "summary"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        if not isinstance(self.severity, AlertSeverity):
            raise ValueError("severity must be an AlertSeverity")
        object.__setattr__(self, "presence_conditions", tuple(self.presence_conditions))
        object.__setattr__(self, "context_conditions", tuple(self.context_conditions))
        if not all(isinstance(c, PresenceCondition) for c in self.presence_conditions):
            raise ValueError("presence_conditions must be PresenceCondition values")
        if not all(isinstance(c, ContextCondition) for c in self.context_conditions):
            raise ValueError("context_conditions must be ContextCondition values")
        if self.prediction_trigger is not None and not isinstance(self.prediction_trigger, PredictionTrigger):
            raise ValueError("prediction_trigger must be a PredictionTrigger")
        if not self.presence_conditions and not self.context_conditions and self.prediction_trigger is None:
            raise ValueError(
                "an alert rule must declare at least one presence, context, or prediction condition"
            )
        _require_confidence(self.minimum_confidence, name="minimum_confidence")


@dataclass(frozen=True)
class Alert:
    """One fired, explainable notification. Immutable once evaluated."""

    alert_id: str
    household_id: str
    severity: AlertSeverity
    event_kind: str
    summary: str
    triggered_at: datetime
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("alert_id", "household_id", "event_kind", "summary"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        if not isinstance(self.severity, AlertSeverity):
            raise ValueError("severity must be an AlertSeverity")
        object.__setattr__(self, "triggered_at", require_aware_utc(self.triggered_at, name="triggered_at"))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not all(isinstance(e, EvidenceRef) for e in self.evidence):
            raise ValueError("evidence must be EvidenceRef values")


__all__ = ["Alert", "AlertRule", "AlertSeverity", "ContextCondition", "PresenceCondition"]
