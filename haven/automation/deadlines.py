"""Evidence-aware deadline observations for resource automations.

Deadline observations are deliberately separate from task records. A task is
one producer of this shape, but conversations, documents, or future providers
can publish the same bounded observation without coupling the scheduler to a
domain store. The observation is a trigger input only; it never grants action
authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from ..core.domain import EvidenceStatus
from ..core.time import require_aware_utc


_INELIGIBLE_STATUSES = frozenset(
    {EvidenceStatus.STALE, EvidenceStatus.FALLBACK, EvidenceStatus.UNAVAILABLE}
)
_RESERVED_FIELDS = frozenset(
    {"deadline_id", "household_id", "source_kind", "source_id", "due_at", "active"}
)


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class AutomationDeadline:
    """One bounded deadline observation eligible for a governed rule.

    ``payload`` is descriptive evidence used by selectors. It cannot replace
    the identity, scope, timing, or active state carried by this value, and
    the outer observation is immutable at the publisher boundary.
    """

    deadline_id: str
    household_id: str
    source_kind: str
    source_id: str
    due_at: datetime
    active: bool = True
    payload: tuple[tuple[str, Any], ...] = ()
    evidence_status: EvidenceStatus = EvidenceStatus.DECLARED

    def __post_init__(self) -> None:
        for field_name in ("deadline_id", "household_id", "source_kind", "source_id"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        object.__setattr__(self, "due_at", require_aware_utc(self.due_at, name="deadline due_at"))
        if not isinstance(self.active, bool):
            raise ValueError("active must be a bool")
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ValueError("evidence_status must be an EvidenceStatus")
        items = self.payload.items() if isinstance(self.payload, Mapping) else self.payload
        frozen: list[tuple[str, Any]] = []
        for key, value in items:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("deadline payload keys must be non-empty strings")
            if key in _RESERVED_FIELDS:
                raise ValueError(f"deadline payload cannot override reserved field: {key}")
            frozen.append((key, deepcopy(value)))
        object.__setattr__(self, "payload", tuple(frozen))

    @property
    def is_eligible_for_automation(self) -> bool:
        return self.active and self.evidence_status not in _INELIGIBLE_STATUSES

    def fire_at(self, offset_minutes: float = 0.0) -> datetime:
        return self.due_at + timedelta(minutes=offset_minutes)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "deadline_id": self.deadline_id,
            "household_id": self.household_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "due_at": self.due_at,
            "active": self.active,
        }
        payload.update(deepcopy(dict(self.payload)))
        return payload


__all__ = ["AutomationDeadline"]
