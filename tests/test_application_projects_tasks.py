"""Application services: authority gates, revisions, wake-set, projection."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.application import ProjectService, TaskService
from haven.domains.projects import ProjectStore
from haven.domains.tasks import BLOCKED, DONE, OPEN, PROPOSED, TaskStore
from haven.knowledge.store import ClaimStore
from haven.ontology.predicates import BELONGS_TO, DEPENDS_ON
from haven.ontology.store import OntologyStore
from haven.resources import ResourceRecord
from haven.resources.store import ResourceStore

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
VISIBLE = ("scope:personal", "scope:home")
PERSONAL = "scope:personal"


@pytest.fixture()
def services():
    with tempfile.TemporaryDirectory() as tmp:
        projects = ProjectStore(Path(tmp) / "projects.db")
        tasks = TaskStore(Path(tmp) / "tasks.db")
        resources = ResourceStore(Path(tmp) / "resources.db")
        ontology = OntologyStore(Path(tmp) / "ontology.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        clock = lambda: NOW  # noqa: E731
        yield (
            ProjectService(
                store=projects, tasks=tasks, resources=resources, ontology=ontology, clock=clock
            ),
            TaskService(
                store=tasks,
                projects=projects,
                resources=resources,
                ontology=ontology,
                clock=clock,
            ),
            projects,
            tasks,
            resources,
            ontology,
            claims,
        )


def _create_project(projects_service, **kwargs):
    result = projects_service.create(
        VISIBLE,
        scope_id=PERSONAL,
        title="UNLV Proposal",
        owner_principal_id="principal:x",
        **kwargs,
    )
    assert result["ok"] is True
    return result["project"]


def test_project_crud_revision_bound(services) -> None:
    projects_service, _, *_ = services
    project = _create_project(projects_service)
    assert project["revision"] == 0

    updated = projects_service.update(
        VISIBLE, project["project_id"], expected_revision=0, title="UNLV Proposal v2"
    )
    assert updated["ok"] is True
    assert updated["project"]["title"] == "UNLV Proposal v2"
    assert updated["project"]["revision"] == 1

    stale = projects_service.update(
        VISIBLE, project["project_id"], expected_revision=0, title="stale write"
    )
    assert stale["ok"] is False
    assert "stale revision" in stale["error"]
    assert projects_service.get(VISIBLE, project["project_id"])["project"]["title"] == (
        "UNLV Proposal v2"
    )


def test_cross_scope_objects_are_invisible_and_immutable(services) -> None:
    projects_service, tasks_service, *_ = services
    project = _create_project(projects_service)
    other_visible = ("scope:elsewhere",)

    assert projects_service.get(other_visible, project["project_id"])["ok"] is False
    denied = projects_service.update(
        other_visible, project["project_id"], expected_revision=0, title="intrusion"
    )
    assert denied["ok"] is False

    created = tasks_service.create(
        other_visible,
        scope_id="scope:elsewhere",
        title="intruder",
        created_by="principal:x",
        project_id=project["project_id"],
    )
    assert created["ok"] is False  # project not visible from that scope set


def test_attach_is_a_relationship_assertion_not_a_copy(services) -> None:
    projects_service, _, _, _, resources, ontology, _ = services
    project = _create_project(projects_service)
    resources.save(
        ResourceRecord(
            resource_id="file:proposal.docx",
            resource_type="file",
            scope_id=PERSONAL,
            provider_id="local_filesystem",
            title="UNLV_Proposal.docx",
            locator="E:/docs/UNLV_Proposal.docx",
            capabilities=(),
            observed_at=NOW,
        )
    )
    result = projects_service.attach(VISIBLE, project["project_id"], "file:proposal.docx")
    assert result["ok"] is True
    # The resource is untouched, still at its own id and locator.
    assert resources.get("file:proposal.docx").locator == "E:/docs/UNLV_Proposal.docx"
    (edge,) = ontology.edges_to(f"project:{project['project_id']}", scope_ids=VISIBLE)
    assert edge.predicate == BELONGS_TO
    assert edge.subject == "file:proposal.docx"

    detached = projects_service.detach(VISIBLE, project["project_id"], "file:proposal.docx")
    assert detached["ok"] is True
    assert projects_service.attached_resources(VISIBLE, project["project_id"])["resources"] == []
    # Detaching revokes the assertion, not the resource.
    assert resources.get("file:proposal.docx") is not None


def test_dependency_wake_set_only_reaches_the_connected_region(services) -> None:
    _, tasks_service, _, tasks, *_ = services
    first = tasks_service.create(VISIBLE, scope_id=PERSONAL, title="step 1", created_by="p")
    second = tasks_service.create(
        VISIBLE,
        scope_id=PERSONAL,
        title="step 2",
        created_by="p",
        dependency_ids=(first["task"]["task_id"],),
    )
    third = tasks_service.create(
        VISIBLE,
        scope_id=PERSONAL,
        title="step 3",
        created_by="p",
        dependency_ids=(second["task"]["task_id"],),
    )
    outsider = tasks_service.create(VISIBLE, scope_id=PERSONAL, title="unrelated", created_by="p")

    assert tasks.get(second["task"]["task_id"]).state == BLOCKED
    assert tasks.get(third["task"]["task_id"]).state == BLOCKED

    completed = tasks_service.complete(
        VISIBLE, first["task"]["task_id"], expected_revision=0, evidence_refs=("file:log.txt",)
    )
    assert completed["ok"] is True
    woken = completed["woken_task_ids"]
    assert second["task"]["task_id"] in woken
    # The region stops at the still-blocked successor: step 3 depends on step
    # 2, which is not terminal yet, so it stays blocked -- completing one
    # task never cascades beyond its immediate connected region.
    assert third["task"]["task_id"] not in woken
    assert tasks.get(second["task"]["task_id"]).state == OPEN
    assert tasks.get(third["task"]["task_id"]).state == BLOCKED
    assert tasks.get(outsider["task"]["task_id"]).state == OPEN
    assert outsider["task"]["task_id"] not in woken

    # Completing step 2 wakes step 3 within the same bounded recompute.
    second_done = tasks_service.complete(
        VISIBLE, second["task"]["task_id"], expected_revision=1, evidence_refs=()
    )
    assert second_done["ok"] is True
    assert second_done["woken_task_ids"] == [third["task"]["task_id"]]
    assert tasks.get(third["task"]["task_id"]).state == OPEN


def test_cycle_refusal_and_removal_wakes(services) -> None:
    _, tasks_service, _, tasks, *_ = services
    one = tasks_service.create(VISIBLE, scope_id=PERSONAL, title="one", created_by="p")
    two = tasks_service.create(VISIBLE, scope_id=PERSONAL, title="two", created_by="p")
    assert tasks_service.add_dependency(VISIBLE, two["task"]["task_id"], one["task"]["task_id"])["ok"]
    cycle = tasks_service.add_dependency(VISIBLE, one["task"]["task_id"], two["task"]["task_id"])
    assert cycle["ok"] is False
    assert "cycle" in cycle["error"]

    removed = tasks_service.remove_dependency(VISIBLE, two["task"]["task_id"], one["task"]["task_id"])
    assert removed["ok"] is True
    assert tasks.get(two["task"]["task_id"]).state == OPEN


def test_completion_spawns_the_next_occurrence_deterministically(services) -> None:
    _, tasks_service, _, tasks, *_ = services
    due = NOW - timedelta(hours=2)
    record = tasks_service.create(
        VISIBLE,
        scope_id=PERSONAL,
        title="Daily backup check",
        created_by="p",
        due_at=due,
        recurrence="daily",
    )
    completed = tasks_service.complete(
        VISIBLE,
        record["task"]["task_id"],
        expected_revision=0,
        evidence_refs=("run:backup-1",),
        evidence_source="provider_observed",
    )
    assert completed["ok"] is True
    done_task = completed["task"]
    assert done_task["state"] == DONE
    assert done_task["completion_evidence_source"] == "provider_observed"
    assert done_task["completion_evidence_refs"] == ["run:backup-1"]
    # The next occurrence is exactly one day after the previous due date --
    # deterministic, never model-derived.
    assert completed["next_occurrence"]["due_at"] == (due + timedelta(days=1)).isoformat()
    assert completed["next_occurrence"]["state"] == OPEN


def test_proposed_tasks_stay_proposed_until_accepted(services) -> None:
    _, tasks_service, _, tasks, *_ = services
    result = tasks_service.create(
        VISIBLE,
        scope_id=PERSONAL,
        title="Model suggested follow-up",
        created_by="p",
        state=PROPOSED,
        source_refs=("conversation:42",),
    )
    assert result["task"]["state"] == PROPOSED
    accepted = tasks_service.update(
        VISIBLE, result["task"]["task_id"], expected_revision=0, state=OPEN
    )
    assert accepted["ok"] is True
    assert accepted["task"]["state"] == OPEN


def test_domain_records_project_into_search(services) -> None:
    projects_service, tasks_service, *_rest = services
    project = _create_project(projects_service)
    tasks_service.create(
        VISIBLE, scope_id=PERSONAL, title="Order calibration kit", created_by="p",
        project_id=project["project_id"],
    )
    resources = _rest[2]
    project_resource = resources.get(f"project:{project['project_id']}")
    assert project_resource is not None
    assert project_resource.resource_type == "project"
    task_resources = [
        record for record in resources.list_by_scope(PERSONAL) if record.resource_type == "task"
    ]
    assert len(task_resources) == 1
    assert task_resources[0].title == "Order calibration kit"
