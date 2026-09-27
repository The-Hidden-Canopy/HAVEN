"""ProjectSource: an active project whose work cannot progress (spec 6).

A project counts as blocked here only when it is `active` and every one of
its open (non-terminal) tasks is itself `blocked` -- a real, derived
condition, not a status field HAVEN invents. A project with no open tasks,
or with at least one task still actionable, is not surfaced: Focus/Upcoming
already cover ordinary project progress.
"""

from __future__ import annotations

from datetime import datetime

from ...domains.projects import ACTIVE
from ...domains.tasks import BLOCKED, TERMINAL_STATES
from ..domain import AttentionActionRef, AttentionItem, AttentionKind, AttentionSeverity, Dismissibility, RouteRef


class ProjectSource:
    def __init__(self, *, projects_store, tasks_store, identity) -> None:
        self._projects = projects_store
        self._tasks = tasks_store
        self._identity = identity

    def collect(self, *, now: datetime, visible_scope_ids: tuple[str, ...]) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for project in self._projects.list_visible(visible_scope_ids):
            if project.status != ACTIVE:
                continue
            tasks = [
                task
                for task in self._tasks.list_visible((project.scope_id,))
                if task.project_id == project.project_id
            ]
            open_tasks = [task for task in tasks if task.state not in TERMINAL_STATES]
            if not open_tasks or any(task.state != BLOCKED for task in open_tasks):
                continue
            why_now = (
                f"All {len(open_tasks)} open task(s) are blocked; "
                "the project cannot progress without a decision."
            )
            items.append(
                AttentionItem(
                    attention_id=f"blocker:project:{project.project_id}",
                    kind=AttentionKind.BLOCKER,
                    severity=AttentionSeverity.NORMAL,
                    title=project.title,
                    summary=why_now,
                    why_now=why_now,
                    source_domain="projects",
                    source_ref=f"project:{project.project_id}",
                    scope_id=project.scope_id,
                    created_at=project.updated_at,
                    evidence_refs=(f"project:{project.project_id}",),
                    route=RouteRef(page="projects", action_hint="review", entity_id=project.project_id, anchor="blocked"),
                    available_actions=(
                        AttentionActionRef(action="route", label="Open project"),
                        AttentionActionRef(action="snooze", label="Snooze"),
                    ),
                    dismissibility=Dismissibility.SNOOZE,
                )
            )
        return items


__all__ = ["ProjectSource"]
