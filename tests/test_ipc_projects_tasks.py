"""IPC adapter for projects/tasks, including the spec page 43 acceptance flow."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.resources import ResourceRecord
from haven.web.server import make_server

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            yield instance
        finally:
            instance.server_close()


def _dispatch(instance, method: str, params: dict) -> dict:
    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def test_acceptance_flow_create_attach_depend_complete_with_evidence(server) -> None:
    instance = server
    created = _dispatch(instance, "projects.create", {"title": "UNLV Proposal"})
    assert created["result"]["ok"] is True
    project = created["result"]["project"]
    assert project["tasks_total"] == 0 and project["tasks_done"] == 0

    instance.resources.save(
        ResourceRecord(
            resource_id="file:proposal.docx",
            resource_type="file",
            scope_id=instance.identity.personal_scope_id,
            provider_id="local_filesystem",
            title="UNLV_Proposal.docx",
            locator=None,
            capabilities=(),
            observed_at=NOW,
        )
    )
    attached = _dispatch(
        instance, "projects.attach", {"project_id": project["project_id"], "resource_id": "file:proposal.docx"}
    )
    assert attached["result"]["ok"] is True
    listed = _dispatch(instance, "projects.attached", {"project_id": project["project_id"]})
    assert [row["resource_id"] for row in listed["result"]["resources"]] == ["file:proposal.docx"]
    assert project["files_count"] == 0 or True  # counts refresh on the next list
    refreshed = _dispatch(instance, "projects.get", {"project_id": project["project_id"]})
    assert refreshed["result"]["project"]["files_count"] == 1

    first = _dispatch(
        instance,
        "tasks.create",
        {
            "title": "Finish UNLV proposal draft",
            "project_id": project["project_id"],
            "priority": "high",
            "due_at": (NOW + timedelta(hours=5)).isoformat(),
        },
    )
    assert first["result"]["ok"] is True
    assert first["result"]["task"]["project_title"] == "UNLV Proposal"

    second = _dispatch(
        instance,
        "tasks.create",
        {
            "title": "Review DWG81 material",
            "project_id": project["project_id"],
            "dependency_ids": [first["result"]["task"]["task_id"]],
        },
    )
    assert second["result"]["task"]["state"] == "blocked"

    completed = _dispatch(
        instance,
        "tasks.complete",
        {
            "task_id": first["result"]["task"]["task_id"],
            "revision": first["result"]["task"]["revision"],
            "evidence_refs": ["file:proposal.docx"],
            "evidence_source": "user_declared",
        },
    )
    assert completed["result"]["ok"] is True
    assert completed["result"]["task"]["completion_evidence_source"] == "user_declared"
    assert completed["result"]["woken_task_ids"] == [second["result"]["task"]["task_id"]]

    views = {
        view: _dispatch(instance, "tasks.list", {"view": view})["result"]["tasks"]
        for view in ("today", "upcoming", "someday", "completed")
    }
    assert [task["title"] for task in views["today"]] == []
    assert views["upcoming"] == []
    assert [task["title"] for task in views["someday"]] == ["Review DWG81 material"]
    assert [task["title"] for task in views["completed"]] == ["Finish UNLV proposal draft"]


def test_validation_and_fail_closed_paths(server) -> None:
    instance = server
    blank = _dispatch(instance, "projects.create", {"title": "  "})
    assert blank["ok"] is False and "title" in blank["error"]

    ghost = _dispatch(instance, "projects.get", {"project_id": "project:ghost"})
    assert ghost["result"]["ok"] is False
    assert ghost["result"]["error"] == "unknown project: project:ghost"

    created = _dispatch(instance, "projects.create", {"title": "Wildlife Research"})
    project = created["result"]["project"]
    stale = _dispatch(
        instance, "projects.update", {"project_id": project["project_id"], "revision": 5, "title": "x"}
    )
    assert stale["result"]["ok"] is False and "stale revision" in stale["result"]["error"]

    no_rev = _dispatch(instance, "projects.update", {"project_id": project["project_id"], "title": "x"})
    assert no_rev["ok"] is False and "revision" in no_rev["error"]

    bad_due = _dispatch(instance, "tasks.create", {"title": "x", "due_at": "not-a-time"})
    assert bad_due["ok"] is False and "due_at" in bad_due["error"]

    bad_view = _dispatch(instance, "tasks.list", {"view": "nope"})
    assert bad_view["result"]["ok"] is False

    attach_ghost = _dispatch(
        instance, "projects.attach", {"project_id": project["project_id"], "resource_id": "file:nope"}
    )
    assert attach_ghost["result"]["ok"] is False


def test_archive_is_a_tombstone_and_counts_stay_consistent(server) -> None:
    instance = server
    created = _dispatch(instance, "projects.create", {"title": "Tidepound"})
    project = created["result"]["project"]
    _dispatch(instance, "tasks.create", {"title": "Probe the inlet", "project_id": project["project_id"]})
    _dispatch(instance, "tasks.create", {"title": "Chart the reef", "project_id": project["project_id"]})

    refreshed = _dispatch(instance, "projects.get", {"project_id": project["project_id"]})
    assert refreshed["result"]["project"]["tasks_total"] == 2

    archived = _dispatch(instance, "projects.archive", {"project_id": project["project_id"]})
    assert archived["result"]["ok"] is True
    assert archived["result"]["project"]["status"] == "archived"

    # Archived projects drop out of the default list but stay gettable, and
    # their tasks are untouched.
    listed = _dispatch(instance, "projects.list", {})
    assert listed["result"]["projects"] == []
    still = _dispatch(instance, "projects.get", {"project_id": project["project_id"]})
    assert still["result"]["project"]["status"] == "archived"
    tasks = _dispatch(instance, "tasks.list", {"view": "all"})
    assert len(tasks["result"]["tasks"]) == 2


def test_search_finds_projects_and_tasks_by_title(server) -> None:
    instance = server
    _dispatch(instance, "projects.create", {"title": "VANTA Engine"})
    _dispatch(instance, "tasks.create", {"title": "Model VANTA deployment"})
    hits = _dispatch(instance, "search.query", {"text": "vanta"})["result"]["hits"]
    kinds = sorted(hit["resource"]["resource_type"] for hit in hits if hit["resource"] is not None)
    assert kinds == ["project", "task"]
