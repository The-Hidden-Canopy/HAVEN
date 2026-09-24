"""Projects domain: the record HAVEN partitions bodies of work by.

Design spec pages 26-27. A project is scope-keyed like every other life
substrate record; `revision` increments on every accepted mutation so a
stale model suggestion can never overwrite a newer user edit (BuildThread
pattern, spec page 11). Deletion is archive/tombstone by default: the
record stays readable (status ``archived``) and attached source resources
are never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

ACTIVE = "active"
PLANNING = "planning"
ON_HOLD = "on_hold"
COMPLETED = "completed"
ARCHIVED = "archived"

PROJECT_STATUSES = frozenset({ACTIVE, PLANNING, ON_HOLD, COMPLETED, ARCHIVED})
OPEN_STATUSES = frozenset({ACTIVE, PLANNING, ON_HOLD})


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    scope_id: str
    title: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    owner_principal_id: str
    parent_project_id: str | None = None
    source: str = "explicit"
    revision: int = 0
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_text(self.project_id, name="project_id"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "title", _require_text(self.title, name="title"))
        if self.status not in PROJECT_STATUSES:
            raise ValueError(f"status must be one of {sorted(PROJECT_STATUSES)}, got {self.status!r}")
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        object.__setattr__(
            self, "owner_principal_id", _require_text(self.owner_principal_id, name="owner_principal_id")
        )
        if self.parent_project_id is not None:
            object.__setattr__(
                self,
                "parent_project_id",
                _require_text(self.parent_project_id, name="parent_project_id"),
            )
        if self.archived_at is not None and (
            self.archived_at.tzinfo is None or self.archived_at.utcoffset() is None
        ):
            raise ValueError("archived_at must be timezone-aware")
        if self.revision < 0:
            raise ValueError("revision must be a non-negative integer")


__all__ = [
    "ACTIVE",
    "ARCHIVED",
    "COMPLETED",
    "ON_HOLD",
    "OPEN_STATUSES",
    "PLANNING",
    "PROJECT_STATUSES",
    "ProjectRecord",
]
