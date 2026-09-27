"""Needs You source adapters: each maps real domain state to AttentionItem,
and excludes what the spec explicitly says does not belong (3.3)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.actions import ResourceActionRequest
from haven.attention.domain import AttentionKind, Dismissibility
from haven.attention.sources import (
    AuthoritySource,
    KnowledgeSource,
    ModelSource,
    ProjectSource,
    TaskSource,
)
from haven.domains.projects import ProjectRecord, ProjectStore
from haven.domains.tasks import TaskRecord, TaskStore
from haven.knowledge import ClaimAdmissionService, ClaimStore, KnowledgeService
from haven.knowledge.claims import ClaimProvenance, ClaimState
from haven.models.jobs import DownloadJobManager
from haven.models.manager import ModelManager
from haven.resources import ResourceStore

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SCOPE = "scope:personal-1"


class _FakeIdentity:
    personal_scope_id = SCOPE

    def visible_scope_ids(self):
        return (SCOPE,)


class _FakePendingRequest:
    def __init__(self, request_id, *, external_connection_id=None):
        self.request_id = request_id
        self.rule_id = "rule-1"
        self.title = "A decision is waiting"
        self.detail = "Garage close needs your approval."
        self.expires_at = NOW + timedelta(minutes=5)
        self.requested_by = None
        self.external_connection_id = external_connection_id


class _FakeDirector:
    def __init__(self, pending):
        self.pending_requests = pending


def test_authority_source_splits_home_and_external_pending():
    director = _FakeDirector(
        (
            _FakePendingRequest("home-1"),
            _FakePendingRequest("ext-1", external_connection_id="conn-1"),
        )
    )
    source = AuthoritySource(director=director, computer_actions=None, identity=_FakeIdentity())
    items = source.collect(now=NOW)

    by_ref = {item.source_ref: item for item in items}
    assert by_ref["pending:home-1"].source_domain == "home"
    assert by_ref["pending:ext-1"].source_domain == "external_agents"
    for item in items:
        assert item.kind is AttentionKind.AUTHORITY
        assert item.dismissibility is Dismissibility.NONE


def test_authority_source_reads_computer_action_pending():
    class _Actions:
        def list_pending(self):
            return (
                ResourceActionRequest(
                    request_id="action-1",
                    household_id="household-1",
                    requested_by="resident-1",
                    provider_id="local_filesystem",
                    action="filesystem.move",
                    resource_id="resource-1",
                    parameters=(("source", "/a"), ("destination", "/b")),
                    justification="tidy up",
                    requested_at=NOW,
                ),
            )

    source = AuthoritySource(director=_FakeDirector(()), computer_actions=_Actions(), identity=_FakeIdentity())
    items = source.collect(now=NOW)

    assert len(items) == 1
    item = items[0]
    assert item.source_domain == "computer"
    assert item.scope_id == "household-1"
    assert "move" in item.why_now.lower()
    assert item.dismissibility is Dismissibility.NONE


def test_model_source_only_surfaces_failed_jobs():
    with tempfile.TemporaryDirectory() as tmp:
        models = ModelManager(Path(tmp) / "models")
        jobs = DownloadJobManager(models)
        jobs.start("http://127.0.0.1:1/dead/model/haven-model.json")

        source = ModelSource(model_jobs=jobs, identity=_FakeIdentity())
        items = source.collect(now=NOW)

        assert len(items) == 1
        assert items[0].kind is AttentionKind.FAILURE
        assert items[0].dismissibility is Dismissibility.SNOOZE
        assert items[0].source_domain == "models"


def _task(task_id, *, state, dependency_ids=(), project_id=None):
    return TaskRecord(
        task_id=task_id,
        scope_id=SCOPE,
        title=task_id,
        detail="",
        state=state,
        created_at=NOW,
        updated_at=NOW,
        created_by="tester",
        project_id=project_id,
        dependency_ids=dependency_ids,
    )


def test_task_source_excludes_merely_due_tasks():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        store.save(_task("due-only", state="open"))
        store.save(_task("blocker", state="open"))
        store.save(_task("blocked", state="blocked", dependency_ids=("blocker",)))

        source = TaskSource(tasks_store=store, identity=_FakeIdentity())
        items = source.collect(now=NOW, visible_scope_ids=(SCOPE,))

        assert [item.source_ref for item in items] == ["task:blocked"]
        assert "blocker" in items[0].why_now.lower() or "Blocked on" in items[0].why_now
        assert items[0].kind is AttentionKind.BLOCKER


def _project(project_id, *, status):
    return ProjectRecord(
        project_id=project_id,
        scope_id=SCOPE,
        title=project_id,
        description="",
        status=status,
        created_at=NOW,
        updated_at=NOW,
        owner_principal_id="owner-1",
    )


def test_project_source_requires_every_open_task_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        projects = ProjectStore(Path(tmp) / "projects.db")
        tasks = TaskStore(Path(tmp) / "tasks.db")

        projects.save(_project("all-blocked", status="active"))
        tasks.save(_task("t1", state="blocked", project_id="all-blocked"))

        projects.save(_project("partially-open", status="active"))
        tasks.save(_task("t2", state="blocked", project_id="partially-open"))
        tasks.save(_task("t3", state="open", project_id="partially-open"))

        projects.save(_project("no-open-tasks", status="active"))

        source = ProjectSource(projects_store=projects, tasks_store=tasks, identity=_FakeIdentity())
        items = source.collect(now=NOW, visible_scope_ids=(SCOPE,))

        assert [item.source_ref for item in items] == ["project:all-blocked"]


def test_knowledge_source_only_surfaces_disputed_claims():
    with tempfile.TemporaryDirectory() as tmp:
        claim_store = ClaimStore(Path(tmp) / "claims.db")
        admission = ClaimAdmissionService(claim_store)
        knowledge = KnowledgeService(resources=ResourceStore(Path(tmp) / "resources.db"), claims=claim_store, clock=lambda: NOW)

        from haven.knowledge.candidates import CandidateClaim

        first = admission.admit(
            CandidateClaim(
                candidate_id="c1",
                scope_id=SCOPE,
                proposition="the launch date is October 1",
                source_refs=("file:proposal.md",),
                evidence_refs=("file:proposal.md#line:1",),
                provenance=ClaimProvenance.DOCUMENT_STATED,
                proposed_confidence=0.9,
                extracted_at=NOW,
            )
        )
        admission.admit(
            CandidateClaim(
                candidate_id="c2",
                scope_id=SCOPE,
                proposition="the launch date is October 2",
                source_refs=("file:notes.md",),
                evidence_refs=("file:notes.md#line:1",),
                provenance=ClaimProvenance.DOCUMENT_STATED,
                proposed_confidence=0.9,
                extracted_at=NOW,
                contradicts=(first.claim.claim_id,),
            )
        )
        assert claim_store.get(first.claim.claim_id).state == ClaimState.DISPUTED

        source = KnowledgeSource(knowledge=knowledge, identity=_FakeIdentity())
        items = source.collect(now=NOW, visible_scope_ids=(SCOPE,))

        assert len(items) == 2
        assert all(item.kind is AttentionKind.CONFLICT for item in items)
        assert all(item.dismissibility is Dismissibility.DISMISS for item in items)
