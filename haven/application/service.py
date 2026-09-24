"""ProjectService and TaskService: thin, fail-closed façades over the domain stores.

Both services answer with the house ``{"ok": ..., "error": ...}`` envelope
and carry the domain wire dicts (the store codecs double as wire shapes).
Adapters pass the principal's membership-derived visible scopes into every
call; the services intersect, never widen.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from haven.domains.projects import ACTIVE, ARCHIVED, OPEN_STATUSES, ProjectRecord
from haven.domains.projects.store import ProjectStore, project_to_dict
from haven.domains.tasks import (
    BLOCKED,
    DONE,
    OPEN,
    PROPOSED,
    TERMINAL_STATES,
    TaskRecord,
)
from haven.domains.tasks.store import TaskStore, task_to_dict
from haven.knowledge.claims import ClaimState
from haven.ontology.assertions import OntologyAssertion
from haven.ontology.predicates import BELONGS_TO, DEPENDS_ON
from haven.ontology.store import OntologyStore
from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


def _fail(reason: str) -> dict:
    return {"ok": False, "error": reason}


def _stale(found: int, expected: int) -> dict:
    return _fail(f"stale revision: the record is at revision {found}, not {expected}")


class ProjectService:
    """Projects lifecycle + resource attachment (relationship assertions)."""

    def __init__(
        self,
        *,
        store: ProjectStore,
        tasks: TaskStore,
        resources: ResourceStore,
        ontology: OntologyStore,
        clock=_DEFAULT_CLOCK,
        mutation_listener=None,
    ) -> None:
        self._store = store
        self._tasks = tasks
        self._resources = resources
        self._ontology = ontology
        self._clock = clock
        self._mutation_listener = mutation_listener

    def set_mutation_listener(self, listener) -> None:
        self._mutation_listener = listener

    def _emit(self, kind: str, record) -> None:
        listener = self._mutation_listener
        if listener is not None:
            listener(kind, record)

    # -- reads ---------------------------------------------------------------

    def get(self, visible: tuple[str, ...], project_id: str) -> dict:
        record = self._store.get(project_id)
        if record is None or record.scope_id not in visible:
            return _fail(f"unknown project: {project_id}")
        return {"ok": True, "project": self._wire(record, visible)}

    def list(self, visible: tuple[str, ...], *, status: str | None = None) -> dict:
        records = self._store.list_visible(visible)
        if status is not None:
            if status == ARCHIVED:
                records = self._store.list_visible(visible, include_archived=True)
            records = tuple(record for record in records if record.status == status)
        return {"ok": True, "projects": [self._wire(record, visible) for record in records]}

    def _wire(self, record: ProjectRecord, visible: tuple[str, ...]) -> dict:
        payload = project_to_dict(record)
        tasks = [
            task
            for task in self._tasks.list_visible(visible)
            if task.project_id == record.project_id
        ]
        payload["tasks_total"] = len(tasks)
        payload["tasks_done"] = sum(1 for task in tasks if task.state == DONE)
        payload["tasks_blocked"] = sum(1 for task in tasks if task.state == BLOCKED)
        payload["files_count"] = len(
            self._ontology.edges_to(
                f"project:{record.project_id}", scope_ids=visible
            )
        )
        payload["people_ids"] = sorted(
            {
                task.assignee_person_id
                for task in tasks
                if task.assignee_person_id is not None
            }
        )
        return payload

    # -- mutations -------------------------------------------------------------

    def create(
        self,
        visible: tuple[str, ...],
        *,
        scope_id: str,
        title: str,
        description: str = "",
        status: str = ACTIVE,
        parent_project_id: str | None = None,
        source: str = "explicit",
        owner_principal_id: str,
    ) -> dict:
        if scope_id not in visible:
            return _fail("the target scope is not visible to this principal")
        if not isinstance(title, str) or not title.strip():
            return _fail("a non-empty 'title' is required")
        if parent_project_id is not None:
            parent = self._store.get(parent_project_id)
            if parent is None or parent.scope_id not in visible:
                return _fail(f"unknown parent project: {parent_project_id}")
        now = self._clock()
        record = ProjectRecord(
            project_id=f"project:{uuid4()}",
            scope_id=scope_id,
            title=title.strip(),
            description=description.strip(),
            status=status,
            created_at=now,
            updated_at=now,
            owner_principal_id=owner_principal_id,
            parent_project_id=parent_project_id,
            source=source,
        )
        self._store.save(record)
        self._project(record)
        self._emit("project", record)
        return {"ok": True, "project": self._wire(record, visible)}

    def update(
        self,
        visible: tuple[str, ...],
        project_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict:
        record = self._visible_record(visible, project_id)
        if isinstance(record, dict):
            return record
        if record.revision != expected_revision:
            return _stale(record.revision, expected_revision)
        try:
            updated = replace(
                record,
                title=record.title if title is None else title.strip(),
                description=record.description if description is None else description.strip(),
                status=record.status if status is None else status,
                updated_at=self._clock(),
                revision=record.revision + 1,
            )
        except ValueError as exc:
            return _fail(str(exc))
        self._store.save(updated)
        self._project(updated)
        self._emit("project", updated)
        return {"ok": True, "project": self._wire(updated, visible)}

    def archive(self, visible: tuple[str, ...], project_id: str) -> dict:
        """Tombstone the project; attached resources and tasks are untouched."""

        record = self._visible_record(visible, project_id)
        if isinstance(record, dict):
            return record
        if record.status == ARCHIVED:
            return {"ok": True, "project": self._wire(record, visible)}
        updated = replace(
            record,
            status=ARCHIVED,
            archived_at=self._clock(),
            updated_at=self._clock(),
            revision=record.revision + 1,
        )
        self._store.save(updated)
        self._project(updated)
        self._emit("project", updated)
        return {"ok": True, "project": self._wire(updated, visible)}

    def attach(self, visible: tuple[str, ...], project_id: str, resource_id: str) -> dict:
        """Assert the relationship; the source resource is never moved/copied."""

        record = self._visible_record(visible, project_id)
        if isinstance(record, dict):
            return record
        resource = self._resources.get(resource_id)
        if resource is None or resource.scope_id not in visible:
            return _fail(f"unknown resource: {resource_id}")
        assertion = OntologyAssertion(
            assertion_id=f"assert:project-attach:{record.project_id}:{resource.resource_id}",
            subject=resource.resource_id,
            predicate=BELONGS_TO,
            object=f"project:{record.project_id}",
            scope_id=record.scope_id,
            state=ClaimState.REPORTED,
            created_at=self._clock(),
            source_ref="haven.projects",
        )
        self._ontology.save(assertion)
        return {"ok": True, "assertion": {"assertion_id": assertion.assertion_id, "resource_id": resource.resource_id}}

    def detach(self, visible: tuple[str, ...], project_id: str, resource_id: str) -> dict:
        record = self._visible_record(visible, project_id)
        if isinstance(record, dict):
            return record
        removed = self._ontology.remove(
            f"assert:project-attach:{record.project_id}:{resource_id}"
        )
        if not removed:
            return _fail(f"resource is not attached to this project: {resource_id}")
        return {"ok": True, "detached": resource_id}

    def attached_resources(self, visible: tuple[str, ...], project_id: str) -> dict:
        record = self._visible_record(visible, project_id)
        if isinstance(record, dict):
            return record
        edges = self._ontology.edges_to(f"project:{record.project_id}", scope_ids=visible)
        rows = []
        for edge in edges:
            resource = self._resources.get(edge.subject)
            rows.append(
                {
                    "resource_id": edge.subject,
                    "title": resource.title if resource is not None else edge.subject,
                    "resource_type": resource.resource_type if resource is not None else "unknown",
                }
            )
        return {"ok": True, "resources": rows}

    def _visible_record(self, visible: tuple[str, ...], project_id: str):
        record = self._store.get(project_id)
        if record is None or record.scope_id not in visible:
            return _fail(f"unknown project: {project_id}")
        return record

    # -- projection ---------------------------------------------------------

    def _project(self, record: ProjectRecord) -> None:
        """Domain record -> searchable resource (the projection idiom)."""

        self._resources.save(
            ResourceRecord(
                resource_id=f"project:{record.project_id}",
                resource_type="project",
                scope_id=record.scope_id,
                provider_id="haven.projects",
                title=record.title,
                locator=None,
                capabilities=(),
                observed_at=record.updated_at,
                metadata=(
                    ("status", record.status),
                    ("project_id", record.project_id),
                ),
                stale=record.status == ARCHIVED,
            )
        )


class TaskService:
    """Tasks lifecycle: revision-bound mutations, deterministic recurrence,
    dependency wake-set (blocked propagation to dependents only)."""

    def __init__(
        self,
        *,
        store: TaskStore,
        projects: ProjectStore,
        resources: ResourceStore,
        ontology: OntologyStore,
        clock=_DEFAULT_CLOCK,
        mutation_listener=None,
    ) -> None:
        self._store = store
        self._projects = projects
        self._resources = resources
        self._ontology = ontology
        self._clock = clock
        self._mutation_listener = mutation_listener

    def set_mutation_listener(self, listener) -> None:
        self._mutation_listener = listener

    def _emit(self, kind: str, record) -> None:
        listener = self._mutation_listener
        if listener is not None:
            listener(kind, record)

    # -- reads ---------------------------------------------------------------

    def get(self, visible: tuple[str, ...], task_id: str) -> dict:
        record = self._visible_record(visible, task_id)
        if isinstance(record, dict):
            return record
        return {"ok": True, "task": self._wire(record, visible)}

    def list(self, visible: tuple[str, ...], *, view: str = "all") -> dict:
        if view not in ("all", "today", "upcoming", "someday", "completed"):
            return _fail(f"unknown task view: {view}")
        now = self._clock()
        records = self._store.list_visible(visible)
        if view == "completed":
            records = tuple(record for record in records if record.is_terminal)
        else:
            live = tuple(record for record in records if not record.is_terminal)
            if view == "today":
                records = tuple(
                    record
                    for record in live
                    if record.due_at is not None and record.due_at <= _end_of_day(now)
                )
            elif view == "upcoming":
                records = tuple(
                    record
                    for record in live
                    if record.due_at is not None and record.due_at > _end_of_day(now)
                )
            else:  # someday
                records = tuple(record for record in live if record.due_at is None)
        return {"ok": True, "tasks": [self._wire(record, visible) for record in records]}

    def _wire(self, record: TaskRecord, visible: tuple[str, ...]) -> dict:
        payload = task_to_dict(record)
        if record.project_id is not None:
            project = self._projects.get(record.project_id)
            payload["project_title"] = project.title if project is not None else None
        payload["blocked_by"] = [
            dependency_id
            for dependency_id in record.dependency_ids
            if (dependency := self._store.get(dependency_id)) is not None
            and not dependency.is_terminal
        ]
        return payload

    # -- mutations -------------------------------------------------------------

    def create(
        self,
        visible: tuple[str, ...],
        *,
        scope_id: str,
        title: str,
        created_by: str,
        detail: str = "",
        project_id: str | None = None,
        priority: str | None = None,
        due_at: datetime | None = None,
        recurrence: str | None = None,
        assignee_person_id: str | None = None,
        dependency_ids: tuple[str, ...] = (),
        source_refs: tuple[str, ...] = (),
        state: str = OPEN,
    ) -> dict:
        if scope_id not in visible:
            return _fail("the target scope is not visible to this principal")
        if not isinstance(title, str) or not title.strip():
            return _fail("a non-empty 'title' is required")
        project_error = self._check_project(visible, scope_id, project_id)
        if project_error is not None:
            return project_error
        dependency_error, dependencies = self._check_dependencies(visible, dependency_ids)
        if dependency_error is not None:
            return dependency_error
        try:
            now = self._clock()
            record = TaskRecord(
                task_id=f"task:{uuid4()}",
                scope_id=scope_id,
                title=title.strip(),
                detail=detail.strip(),
                state=state,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                project_id=project_id,
                priority=priority,
                due_at=due_at,
                recurrence=recurrence,
                assignee_person_id=assignee_person_id,
                dependency_ids=dependency_ids,
                source_refs=source_refs,
            )
            # Model-derived next steps stay PROPOSED until accepted; a task
            # with unmet dependencies starts BLOCKED.
            if any(not dependency.is_terminal for dependency in dependencies):
                record = replace(record, state=BLOCKED)
        except ValueError as exc:
            return _fail(str(exc))
        self._store.save(record)
        self._project(record)
        self._record_dependency_edges(record)
        self._emit("task", record)
        return {"ok": True, "task": self._wire(record, visible)}

    def update(
        self,
        visible: tuple[str, ...],
        task_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        detail: str | None = None,
        state: str | None = None,
        priority: str | None = None,
        due_at: datetime | None = None,
        assignee_person_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        record = self._visible_record(visible, task_id)
        if isinstance(record, dict):
            return record
        if record.revision != expected_revision:
            return _stale(record.revision, expected_revision)
        if record.is_terminal:
            return _fail("terminal tasks are immutable; create a new task instead")
        if project_id is not None:
            project_error = self._check_project(visible, record.scope_id, project_id)
            if project_error is not None:
                return project_error
        try:
            updated = replace(
                record,
                title=record.title if title is None else title.strip(),
                detail=record.detail if detail is None else detail.strip(),
                state=record.state if state is None else state,
                priority=record.priority if priority is None else priority,
                due_at=record.due_at if due_at is None else due_at,
                assignee_person_id=(
                    record.assignee_person_id if assignee_person_id is None else assignee_person_id
                ),
                project_id=record.project_id if project_id is None else project_id,
                updated_at=self._clock(),
                revision=record.revision + 1,
            )
        except ValueError as exc:
            return _fail(str(exc))
        self._store.save(updated)
        self._project(updated)
        self._emit("task", updated)
        woken = self._recompute_dependents(visible, updated.task_id)
        payload = {"ok": True, "task": self._wire(updated, visible)}
        if woken:
            payload["woken_task_ids"] = woken
        return payload

    def complete(
        self,
        visible: tuple[str, ...],
        task_id: str,
        *,
        expected_revision: int,
        evidence_refs: tuple[str, ...] = (),
        evidence_source: str = "user_declared",
    ) -> dict:
        """Complete with evidence; deterministic next occurrence on recurrence."""

        record = self._visible_record(visible, task_id)
        if isinstance(record, dict):
            return record
        if record.revision != expected_revision:
            return _stale(record.revision, expected_revision)
        if record.is_terminal:
            return _fail("task is already terminal")
        if any(
            (dependency := self._store.get(dependency_id)) is not None
            and not dependency.is_terminal
            for dependency_id in record.dependency_ids
        ):
            return _fail("task has unfinished dependencies")
        now = self._clock()
        completed = replace(
            record,
            state=DONE,
            completed_at=now,
            completion_evidence_refs=tuple(evidence_refs),
            completion_evidence_source=evidence_source,
            updated_at=now,
            revision=record.revision + 1,
        )
        self._store.save(completed)
        self._project(completed)
        self._emit("task", completed)
        payload: dict = {"ok": True, "task": self._wire(completed, visible)}
        next_occurrence = self._spawn_next_occurrence(visible, completed)
        if next_occurrence is not None:
            payload["next_occurrence"] = next_occurrence
        woken = self._recompute_dependents(visible, completed.task_id)
        if woken:
            payload["woken_task_ids"] = woken
        return payload

    def add_dependency(
        self, visible: tuple[str, ...], task_id: str, depends_on_task_id: str
    ) -> dict:
        record = self._visible_record(visible, task_id)
        if isinstance(record, dict):
            return record
        dependency = self._visible_record(visible, depends_on_task_id)
        if isinstance(dependency, dict):
            return dependency
        if depends_on_task_id in record.dependency_ids:
            return {"ok": True, "task": self._wire(record, visible)}
        if self._reaches(visible, depends_on_task_id, task_id):
            return _fail("that dependency would create a cycle")
        if record.is_terminal or dependency.task_id == record.task_id:
            return _fail("terminal or self dependencies are not allowed")
        updated = replace(
            record,
            dependency_ids=record.dependency_ids + (depends_on_task_id,),
            state=BLOCKED if not dependency.is_terminal else record.state,
            updated_at=self._clock(),
            revision=record.revision + 1,
        )
        self._store.save(updated)
        self._project(updated)
        self._record_dependency_edges(updated)
        return {"ok": True, "task": self._wire(updated, visible)}

    def remove_dependency(
        self, visible: tuple[str, ...], task_id: str, depends_on_task_id: str
    ) -> dict:
        record = self._visible_record(visible, task_id)
        if isinstance(record, dict):
            return record
        if depends_on_task_id not in record.dependency_ids:
            return _fail(f"task does not depend on {depends_on_task_id}")
        updated = replace(
            record,
            dependency_ids=tuple(
                dependency_id
                for dependency_id in record.dependency_ids
                if dependency_id != depends_on_task_id
            ),
            updated_at=self._clock(),
            revision=record.revision + 1,
        )
        self._store.save(updated)
        self._project(updated)
        self._ontology.remove(
            f"assert:task-dep:{updated.task_id}:{depends_on_task_id}"
        )
        woken = self._recompute_dependents(visible, updated.task_id, include_self=True)
        payload = {"ok": True, "task": self._wire(updated, visible)}
        if woken:
            payload["woken_task_ids"] = woken
        return payload

    # -- dependency region (spec page 11: wake only the connected region) --------

    def _recompute_dependents(
        self, visible: tuple[str, ...], changed_task_id: str, *, include_self: bool = False
    ) -> list[str]:
        """Recompute blocked state across the dependents reachable from one
        changed task -- nothing outside that reverse-dependency region is
        touched. ``include_self`` also recomputes the changed task itself
        (dependency removal can unblock it)."""

        woken: list[str] = []
        frontier = [changed_task_id]
        seen: set[str] = set()
        first = True
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            if first and include_self:
                # The changed task itself: recompute before walking dependents.
                record = self._store.get(current)
                if record is not None and not record.is_terminal:
                    self._recompute_one(record, woken)
            first = False
            for dependent in self._store.dependents_of(current, scope_ids=visible):
                if dependent.is_terminal:
                    continue
                self._recompute_one(dependent, woken)
                frontier.append(dependent.task_id)
        return woken

    def _recompute_one(self, record, woken: list) -> None:
        blocked = any(
            (dependency := self._store.get(dependency_id)) is not None
            and not dependency.is_terminal
            for dependency_id in record.dependency_ids
        )
        new_state = BLOCKED if blocked else (OPEN if record.state == BLOCKED else record.state)
        if new_state != record.state:
            updated = replace(
                record,
                state=new_state,
                updated_at=self._clock(),
                revision=record.revision + 1,
            )
            self._store.save(updated)
            self._project(updated)
            woken.append(updated.task_id)

    def _reaches(self, visible: tuple[str, ...], start: str, target: str) -> bool:
        """True when ``target`` is reachable from ``start`` over dependency
        edges (used to refuse cycles)."""

        frontier = [start]
        seen: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            record = self._store.get(current)
            if record is None:
                continue
            frontier.extend(record.dependency_ids)
        return False

    def _check_project(self, visible: tuple[str, ...], scope_id: str, project_id: str | None):
        if project_id is None:
            return None
        project = self._projects.get(project_id)
        if project is None or project.scope_id not in visible:
            return _fail(f"unknown project: {project_id}")
        if project.scope_id != scope_id:
            return _fail("a task must live in the same scope as its project")
        if project.status == ARCHIVED:
            return _fail("archived projects cannot take new tasks")
        return None

    def _check_dependencies(self, visible: tuple[str, ...], dependency_ids: tuple[str, ...]):
        dependencies = []
        for dependency_id in dependency_ids:
            dependency = self._store.get(dependency_id)
            if dependency is None or dependency.scope_id not in visible:
                return _fail(f"unknown dependency: {dependency_id}"), ()
            dependencies.append(dependency)
        return None, tuple(dependencies)

    def _visible_record(self, visible: tuple[str, ...], task_id: str):
        record = self._store.get(task_id)
        if record is None or record.scope_id not in visible:
            return _fail(f"unknown task: {task_id}")
        return record

    # -- projection ---------------------------------------------------------

    def _project(self, record: TaskRecord) -> None:
        metadata: list[tuple[str, str]] = [
            ("state", record.state),
            ("task_id", record.task_id),
        ]
        if record.priority is not None:
            metadata.append(("priority", record.priority))
        if record.project_id is not None:
            metadata.append(("project_id", record.project_id))
        if record.due_at is not None:
            metadata.append(("due_at", record.due_at.isoformat()))
        self._resources.save(
            ResourceRecord(
                resource_id=f"task:{record.task_id}",
                resource_type="task",
                scope_id=record.scope_id,
                provider_id="haven.tasks",
                title=record.title,
                locator=None,
                capabilities=(),
                observed_at=record.updated_at,
                metadata=tuple(metadata),
                stale=record.state == "cancelled",
            )
        )

    def _record_dependency_edges(self, record: TaskRecord) -> None:
        for dependency_id in record.dependency_ids:
            self._ontology.save(
                OntologyAssertion(
                    assertion_id=f"assert:task-dep:{record.task_id}:{dependency_id}",
                    subject=f"task:{record.task_id}",
                    predicate=DEPENDS_ON,
                    object=f"task:{dependency_id}",
                    scope_id=record.scope_id,
                    state=ClaimState.REPORTED,
                    created_at=self._clock(),
                    source_ref="haven.tasks",
                )
            )

    def _spawn_next_occurrence(self, visible: tuple[str, ...], completed: TaskRecord):
        if completed.recurrence is None or completed.due_at is None:
            return None
        delta = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}.get(
            completed.recurrence
        )
        if completed.recurrence == "monthly":
            due = completed.due_at + timedelta(days=28)
            while due.month == completed.due_at.month:
                due += timedelta(days=1)
        elif delta is not None:
            due = completed.due_at + delta
        else:
            return None
        now = self._clock()
        occurrence = TaskRecord(
            task_id=f"task:{uuid4()}",
            scope_id=completed.scope_id,
            title=completed.title,
            detail=completed.detail,
            state=OPEN,
            created_at=now,
            updated_at=now,
            created_by=completed.created_by,
            project_id=completed.project_id,
            priority=completed.priority,
            due_at=due,
            recurrence=completed.recurrence,
            assignee_person_id=completed.assignee_person_id,
            source_refs=(f"task:{completed.task_id}",),
        )
        self._store.save(occurrence)
        self._project(occurrence)
        return self._wire(occurrence, visible)


def _end_of_day(now: datetime) -> datetime:
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


__all__ = ["ProjectService", "TaskService"]
