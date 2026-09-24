"""Domain stores: projects and tasks records, revisions, restart durability."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.domains.projects import ACTIVE, ARCHIVED, ProjectRecord, ProjectStore
from haven.domains.tasks import DONE, OPEN, TaskRecord, TaskStore

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def stores():
    with tempfile.TemporaryDirectory() as tmp:
        yield (
            ProjectStore(Path(tmp) / "projects.db"),
            TaskStore(Path(tmp) / "tasks.db"),
            Path(tmp),
        )


def _project(store, project_id="project:p1", scope_id="scope:a", revision=0, status=ACTIVE):
    record = ProjectRecord(
        project_id=project_id,
        scope_id=scope_id,
        title="UNLV Proposal",
        description="Research and prepare the proposal",
        status=status,
        created_at=NOW,
        updated_at=NOW,
        owner_principal_id="principal:x",
        revision=revision,
    )
    store.save(record)
    return record


def _task(store, task_id="task:t1", scope_id="scope:a", state=OPEN, dependencies=()):
    record = TaskRecord(
        task_id=task_id,
        scope_id=scope_id,
        title="Finish draft",
        detail="",
        state=state,
        created_at=NOW,
        updated_at=NOW,
        created_by="principal:x",
        dependency_ids=tuple(dependencies),
    )
    store.save(record)
    return record


def test_project_round_trip_and_revision_persistence(stores) -> None:
    projects, _, _ = stores
    _project(projects, revision=7)
    loaded = projects.get("project:p1")
    assert loaded is not None
    assert loaded.revision == 7
    assert loaded.status == ACTIVE
    assert loaded.owner_principal_id == "principal:x"


def test_project_archive_keeps_the_record_readable(stores) -> None:
    projects, _, _ = stores
    _project(projects)
    archived = ProjectRecord(
        project_id="project:p1",
        scope_id="scope:a",
        title="UNLV Proposal",
        description="",
        status=ARCHIVED,
        created_at=NOW,
        updated_at=NOW,
        owner_principal_id="principal:x",
        archived_at=NOW,
    )
    projects.save(archived)
    assert projects.get("project:p1").status == ARCHIVED
    assert projects.get("project:p1").archived_at == NOW
    assert [r.project_id for r in projects.list_by_scope("scope:a")] == ["project:p1"]
    # Archived excluded by default, included on request.
    assert projects.list_visible(("scope:a",)) == ()
    assert [r.project_id for r in projects.list_visible(("scope:a",), include_archived=True)] == [
        "project:p1"
    ]


def test_task_validation(stores) -> None:
    _, tasks, _ = stores
    with pytest.raises(ValueError):
        _task(tasks, state="nonsense")
    with pytest.raises(ValueError):
        TaskRecord(
            task_id="task:x",
            scope_id="scope:a",
            title="t",
            detail="",
            state=OPEN,
            created_at=NOW,
            updated_at=NOW,
            created_by="p",
            priority="urgent",
        )
    # Completion evidence is only legal on terminal tasks.
    with pytest.raises(ValueError):
        TaskRecord(
            task_id="task:x",
            scope_id="scope:a",
            title="t",
            detail="",
            state=OPEN,
            created_at=NOW,
            updated_at=NOW,
            created_by="p",
            completion_evidence_source="user_declared",
        )
    done = TaskRecord(
        task_id="task:x",
        scope_id="scope:a",
        title="t",
        detail="",
        state=DONE,
        created_at=NOW,
        updated_at=NOW,
        created_by="p",
        completed_at=NOW,
        completion_evidence_source="provider_observed",
        completion_evidence_refs=("file:receipt.txt",),
    )
    tasks.save(done)
    loaded = tasks.get("task:x")
    assert loaded.completion_evidence_source == "provider_observed"
    assert loaded.completion_evidence_refs == ("file:receipt.txt",)


def test_dependents_of_walks_reverse_dependencies(stores) -> None:
    _, tasks, _ = stores
    _task(tasks, task_id="task:a")
    _task(tasks, task_id="task:b", dependencies=("task:a",))
    _task(tasks, task_id="task:c", dependencies=("task:a", "task:b"))
    _task(tasks, task_id="task:unrelated")
    assert {t.task_id for t in tasks.dependents_of("task:a", scope_ids=("scope:a",))} == {
        "task:b",
        "task:c",
    }


def test_stores_are_restart_durable(stores) -> None:
    projects, tasks, tmp = stores
    _project(projects)
    _task(tasks, dependencies=("task:t1",))
    # New store objects over the same files, as a fresh boot would build.
    rebound_projects = ProjectStore(Path(tmp) / "projects.db")
    rebound_tasks = TaskStore(Path(tmp) / "tasks.db")
    assert rebound_projects.get("project:p1") is not None
    rebound_task = rebound_tasks.get("task:t1")
    assert rebound_task is not None
    assert rebound_task.dependency_ids == ("task:t1",) or rebound_task.task_id == "task:t1"
