"""Tasks domain: the record HAVEN partitions commitments by.

Design spec pages 26-27. States are the spec's objective-graph
vocabulary; `revision` is BuildThread-bound like projects. Completion
keeps its evidence source (user-declared vs provider-observed) so a
checked-off task can always answer "how do we know".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PROPOSED = "proposed"
OPEN = "open"
IN_PROGRESS = "in_progress"
BLOCKED = "blocked"
DONE = "done"
CANCELLED = "cancelled"

TASK_STATES = frozenset({PROPOSED, OPEN, IN_PROGRESS, BLOCKED, DONE, CANCELLED})
TERMINAL_STATES = frozenset({DONE, CANCELLED})
PRIORITIES = frozenset({"high", "medium", "low"})
RECURRENCES = frozenset({"daily", "weekly", "monthly"})


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    scope_id: str
    title: str
    detail: str
    state: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    revision: int = 0
    project_id: str | None = None
    priority: str | None = None
    due_at: datetime | None = None
    recurrence: str | None = None
    assignee_person_id: str | None = None
    dependency_ids: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    completed_at: datetime | None = None
    completion_evidence_refs: tuple[str, ...] = ()
    completion_evidence_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_text(self.task_id, name="task_id"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "title", _require_text(self.title, name="title"))
        if self.state not in TASK_STATES:
            raise ValueError(f"state must be one of {sorted(TASK_STATES)}, got {self.state!r}")
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        object.__setattr__(self, "created_by", _require_text(self.created_by, name="created_by"))
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(PRIORITIES)}, got {self.priority!r}")
        if self.recurrence is not None and self.recurrence not in RECURRENCES:
            raise ValueError(f"recurrence must be one of {sorted(RECURRENCES)}, got {self.recurrence!r}")
        for field_name in ("due_at", "completed_at"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        for field_name in ("dependency_ids", "source_refs", "completion_evidence_refs"):
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                object.__setattr__(self, field_name, tuple(value))
            for item in getattr(self, field_name):
                _require_text(item, name=field_name)
        if self.state in TERMINAL_STATES:
            pass
        elif self.completed_at is not None or self.completion_evidence_source is not None:
            raise ValueError("only terminal tasks carry completion evidence")
        if self.completion_evidence_source is not None and self.completion_evidence_source not in (
            "user_declared",
            "provider_observed",
        ):
            raise ValueError("completion_evidence_source must be user_declared or provider_observed")
        if self.revision < 0:
            raise ValueError("revision must be a non-negative integer")

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


__all__ = [
    "BLOCKED",
    "CANCELLED",
    "DONE",
    "IN_PROGRESS",
    "OPEN",
    "PRIORITIES",
    "PROPOSED",
    "RECURRENCES",
    "TASK_STATES",
    "TERMINAL_STATES",
    "TaskRecord",
]
