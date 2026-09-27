"""TaskSource: blocked tasks (spec 6, "TaskSource").

Only a task actually in the `blocked` state is eligible -- a task merely
being due belongs to Upcoming, not Needs You (spec 3.3's explicit
exclusion).
"""

from __future__ import annotations

from datetime import datetime

from ...domains.tasks import BLOCKED, TERMINAL_STATES
from ..domain import AttentionActionRef, AttentionItem, AttentionKind, AttentionSeverity, Dismissibility, RouteRef


class TaskSource:
    def __init__(self, *, tasks_store, identity) -> None:
        self._tasks = tasks_store
        self._identity = identity

    def collect(self, *, now: datetime, visible_scope_ids: tuple[str, ...]) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for task in self._tasks.list_visible(visible_scope_ids):
            if task.state != BLOCKED:
                continue
            blockers = [
                dependency
                for dependency_id in task.dependency_ids
                if (dependency := self._tasks.get(dependency_id)) is not None
                and dependency.state not in TERMINAL_STATES
            ]
            if len(blockers) == 1:
                why_now = f'Blocked on "{blockers[0].title}", which is not yet done.'
            elif blockers:
                why_now = f"Blocked on {len(blockers)} unfinished dependencies."
            else:
                why_now = "Blocked and waiting on a decision or prerequisite."
            items.append(
                AttentionItem(
                    attention_id=f"blocker:task:{task.task_id}",
                    kind=AttentionKind.BLOCKER,
                    severity=AttentionSeverity.NORMAL,
                    title=task.title,
                    summary=why_now,
                    why_now=why_now,
                    source_domain="tasks",
                    source_ref=f"task:{task.task_id}",
                    scope_id=task.scope_id,
                    created_at=task.updated_at,
                    evidence_refs=(f"task:{task.task_id}",),
                    route=RouteRef(page="tasks", action_hint="review", entity_id=task.task_id),
                    available_actions=(
                        AttentionActionRef(action="route", label="Open task"),
                        AttentionActionRef(action="snooze", label="Snooze"),
                    ),
                    dismissibility=Dismissibility.SNOOZE,
                )
            )
        return items


__all__ = ["TaskSource"]
