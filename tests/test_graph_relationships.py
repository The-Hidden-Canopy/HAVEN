"""Relationship graph: deterministic edges, learned candidates, admission policy."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.core.domain import EventType, Principal, RoleTier
from haven.core.store import HavenStore
from haven.domains.projects import ProjectRecord, ProjectStore
from haven.domains.tasks import TaskRecord, TaskStore
from haven.graph import (
    CandidateRelationship,
    Correlator,
    RelationshipAdmissionPolicy,
    RelationshipProjector,
    RelationshipService,
)
from haven.graph.candidates import candidate_id
from haven.graph.deterministic import assertion_id
from haven.knowledge import Claim, ClaimProvenance, ClaimState, ClaimStore
from haven.ontology.predicates import BELONGS_TO, CONTAINS, OWNED_BY, RELATED_TO
from haven.ontology.store import OntologyStore
from haven.resources import ResourceRecord
from haven.resources.store import ResourceStore
from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
VISIBLE = ("scope:personal",)


@pytest.fixture()
def stack():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        ontology = OntologyStore(Path(tmp) / "ontology.db")
        tasks = TaskStore(Path(tmp) / "tasks.db")
        projects = ProjectStore(Path(tmp) / "projects.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        yield tmp, resources, ontology, tasks, projects, claims


def _file(resources, name, scope="scope:personal", locator=None, title=None, rtype="file"):
    record = ResourceRecord(
        resource_id=f"file:{name}",
        resource_type=rtype,
        scope_id=scope,
        provider_id="local_filesystem",
        title=title or name,
        locator=locator,
        capabilities=(),
        observed_at=NOW,
    )
    resources.save(record)
    return record


def test_deterministic_folder_git_window_tab_edges(stack) -> None:
    tmp, resources, ontology, *_ = stack
    root = Path(tmp) / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "notes.txt").write_text("x", encoding="utf-8")
    folder = _file(resources, root.as_posix(), rtype="folder", locator=str(root), title="repo")
    record = _file(resources, str(root / "notes.txt"), locator=str(root / "notes.txt"))

    resources.save(
        ResourceRecord(
            resource_id="window:1", resource_type="window", scope_id="scope:personal",
            provider_id="haven.windows", title="Doc - Word", locator=None, capabilities=(),
            observed_at=NOW, metadata=(("process", "WINWORD.EXE"), ("hwnd", "1")),
        )
    )
    resources.save(
        ResourceRecord(
            resource_id="browsertab:t1", resource_type="browser_tab", scope_id="scope:personal",
            provider_id="haven.browser", title="Tab", locator=None, capabilities=(),
            observed_at=NOW, metadata=(("browser", "chrome"),),
        )
    )
    projector = RelationshipProjector(resources=resources, ontology=ontology)
    written = projector.project_all(visible_scopes=VISIBLE)
    assert written >= 5

    file_edges = [edge for edge in ontology.edges_from(record.resource_id) if edge.predicate == BELONGS_TO]
    assert any(edge.object == folder.resource_id for edge in file_edges)
    assert any(edge.object.startswith("repository:") for edge in file_edges)
    assert ontology.get(assertion_id(folder.resource_id, CONTAINS, record.resource_id)) is not None
    assert any(edge.object == "application:WINWORD.EXE" for edge in ontology.edges_from("window:1"))
    assert any(edge.object == "browser-session:chrome" for edge in ontology.edges_from("browsertab:t1"))

    # Idempotent: a second full pass writes nothing new.
    assert projector.project_all(visible_scopes=VISIBLE) == 0


def test_deterministic_task_claim_and_room_edges(stack) -> None:
    tmp, resources, ontology, tasks, projects, claims = stack
    tasks.save(
        TaskRecord(
            task_id="task:t1", scope_id="scope:personal", title="t", detail="",
            state="open", created_at=NOW, updated_at=NOW, created_by="p",
            project_id="project:p1", assignee_person_id="gerron",
        )
    )
    projects.save(
        ProjectRecord(
            project_id="project:p1", scope_id="scope:personal", title="P", description="",
            status="active", created_at=NOW, updated_at=NOW, owner_principal_id="p",
        )
    )
    claims.save(
        Claim(
            claim_id="claim:c1", scope_id="scope:personal", proposition="x",
            state=ClaimState.REPORTED, source_refs=("file:notes.txt",), evidence_refs=(),
            created_at=NOW, provenance=ClaimProvenance.DOCUMENT_STATED,
            supersedes=("claim:c0",),
        )
    )
    rooms = lambda: ({"scope_id": "scope:personal", "room_id": "office", "devices": ("office_light",)},)  # noqa: E731
    projector = RelationshipProjector(
        resources=resources, ontology=ontology, tasks_store=tasks,
        projects_store=projects, claims_store=claims, rooms_provider=rooms,
    )
    projector.project_all(visible_scopes=VISIBLE)

    assert ontology.get(assertion_id("project:project:p1", CONTAINS, "task:task:t1")) is not None
    assert any(edge.object == "person:gerron" for edge in ontology.edges_from("task:task:t1"))
    assert any(edge.object == "file:notes.txt" for edge in ontology.edges_from("claim:claim:c1"))
    assert any(edge.object == "claim:c0" for edge in ontology.edges_from("claim:claim:c1"))
    assert any(edge.object == "room:office" for edge in ontology.edges_from("device:office_light"))


def test_recompute_region_is_bounded(stack) -> None:
    tmp, resources, ontology, *_ = stack
    _file(resources, "/a/one.txt", locator=str(Path(tmp) / "a" / "one.txt"))
    _file(resources, "/b/two.txt", locator=str(Path(tmp) / "b" / "two.txt"))
    projector = RelationshipProjector(resources=resources, ontology=ontology)
    projector.project_all(visible_scopes=VISIBLE)
    count_after_full = len(ontology.list_by_scope("scope:personal"))

    # A region recompute for one file must not duplicate or rewrite the rest.
    projector.recompute_region("file:/a/one.txt", visible_scopes=VISIBLE)
    assert len(ontology.list_by_scope("scope:personal")) == count_after_full
    assert projector.recompute_region("file:/ghost.txt", visible_scopes=VISIBLE) == 0


def test_correlator_candidates_and_policy_tiers(stack) -> None:
    tmp, resources, ontology, *_ = stack
    _file(resources, "lunar-notes", title="Lunar site preparation notes")
    _file(resources, "lunar-budget", title="Lunar budget preparation")
    correlator = Correlator(resources=resources, clock=lambda: NOW)
    candidates = correlator.candidates(visible_scopes=VISIBLE)
    assert candidates, "shared significant tokens should correlate"
    assert all(candidate.predicate == RELATED_TO for candidate in candidates)
    assert all(0.34 <= candidate.confidence <= 1.0 for candidate in candidates)

    policy = RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW)
    high = CandidateRelationship(
        candidate_id=candidate_id("a", "b", OWNED_BY), subject="a", predicate=OWNED_BY,
        object="b", evidence_refs=("a", "b"), confidence=0.99, scope_id="scope:personal",
        proposed_by="model.fake", created_at=NOW,
    )
    # High-impact: never auto, no matter the confidence.
    assert policy.classify(high) == "needs_review"
    low_strong = CandidateRelationship(
        candidate_id=candidate_id("a", "b", RELATED_TO), subject="a", predicate=RELATED_TO,
        object="b", evidence_refs=("a", "b"), confidence=0.9, scope_id="scope:personal",
        proposed_by="haven.correlation", created_at=NOW,
    )
    assert policy.classify(low_strong) == "needs_review"
    low_weak = CandidateRelationship(
        candidate_id=candidate_id("a", "c", RELATED_TO), subject="a", predicate=RELATED_TO,
        object="c", evidence_refs=("a", "c"), confidence=0.4, scope_id="scope:personal",
        proposed_by="haven.correlation", created_at=NOW,
    )
    assert policy.classify(low_weak) == "needs_review"
    blocked = policy.auto_admit(low_strong)
    assert blocked == {
        "ok": False,
        "error": "automatic relationship admission is disabled; review and admit explicitly",
    }
    assert ontology.list_by_scope("scope:personal") == ()

    assertion = policy.build_assertion(high)
    assert assertion.assertion_id.startswith("assert-learned:")
    # The policy only constructs review material. It cannot write an edge
    # without the governed service transition below.
    assert ontology.list_by_scope("scope:personal") == ()


def test_service_reject_persists_and_requires_explicit_admission(stack) -> None:
    tmp, resources, ontology, *_ = stack
    store = HavenStore(household_id="household:test")
    owner = Principal(actor_id="owner:test", household_id="household:test", role_tier=RoleTier.OWNER)
    _file(resources, "alpha", title="alpha bravo charlie")
    _file(resources, "bravo", title="bravo charlie delta echo")
    service = RelationshipService(
        projector=RelationshipProjector(resources=resources, ontology=ontology),
        correlator=Correlator(resources=resources, clock=lambda: NOW),
        policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
        ontology=ontology,
        state_path=Path(tmp) / "graph.json",
        transition_store=store,
        principal=owner,
    )
    first = service.candidates(visible_scopes=VISIBLE)["candidates"]
    assert first, "expected a needs_review candidate"
    target = first[0]
    rejected = service.reject(candidate_id_value=target["candidate_id"], visible_scopes=VISIBLE)
    assert rejected["ok"] is True

    # Restart durability: the rejection survives a fresh service over the same state file.
    service2 = RelationshipService(
        projector=RelationshipProjector(resources=resources, ontology=ontology),
        correlator=Correlator(resources=resources, clock=lambda: NOW),
        policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
        ontology=ontology,
        state_path=Path(tmp) / "graph.json",
        transition_store=store,
        principal=owner,
    )
    remaining = service2.candidates(visible_scopes=VISIBLE)["candidates"]
    assert all(item["candidate_id"] != target["candidate_id"] for item in remaining)

    admitted = service2.admit(
        candidate_id_value=remaining[0]["candidate_id"],
        visible_scopes=VISIBLE,
        justification="owner confirmed the relationship",
    )
    assert admitted["ok"] is True
    assert store.events[-1].event_type is EventType.RELATIONSHIP_ADMITTED
    edges = service2.edges_for(remaining[0]["subject"], visible_scopes=VISIBLE)["edges"]
    assert any("learned:" in (edge.get("provenance") or "") for edge in edges)


def test_relationship_admission_fail_closed_for_justification_role_and_household(stack) -> None:
    tmp, resources, ontology, *_ = stack
    _file(resources, "alpha", title="alpha bravo charlie")
    _file(resources, "bravo", title="bravo charlie delta echo")
    store = HavenStore(household_id="household:test")

    def make_service(principal):
        return RelationshipService(
            projector=RelationshipProjector(resources=resources, ontology=ontology),
            correlator=Correlator(resources=resources, clock=lambda: NOW),
            policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
            ontology=ontology,
            state_path=Path(tmp) / "graph.json",
            transition_store=store,
            principal=principal,
        )

    target = make_service(
        Principal(actor_id="owner:test", household_id="household:test", role_tier=RoleTier.OWNER)
    ).candidates(visible_scopes=VISIBLE)["candidates"][0]
    member = make_service(
        Principal(actor_id="member:test", household_id="household:test", role_tier=RoleTier.MEMBER)
    )
    assert member.admit(
        candidate_id_value=target["candidate_id"], visible_scopes=VISIBLE, justification=""
    ) == {"ok": False, "error": "relationship admission requires a non-empty justification"}
    assert member.admit(
        candidate_id_value=target["candidate_id"],
        visible_scopes=VISIBLE,
        justification="member tried to admit",
    ) == {"ok": False, "error": "only an owner can admit a household relationship"}

    foreign = make_service(
        Principal(actor_id="owner:other", household_id="household:other", role_tier=RoleTier.OWNER)
    )
    assert foreign.admit(
        candidate_id_value=target["candidate_id"],
        visible_scopes=VISIBLE,
        justification="foreign household attempt",
    ) == {"ok": False, "error": "relationship admission household scope does not match"}
    assert ontology.list_by_scope("scope:personal") == ()
    assert store.events == ()


def test_server_does_not_grant_graph_owner_power_before_onboarding(stack) -> None:
    tmp, *_ = stack
    server, _ = make_server(0, data_dir=Path(tmp) / "server", clock=lambda: NOW)
    try:
        scope_id = server.identity.personal_scope_id
        for resource_id, title in (
            ("file:one", "alpha bravo charlie"),
            ("file:two", "bravo charlie delta echo"),
        ):
            server.resources.save(
                ResourceRecord(
                    resource_id=resource_id,
                    resource_type="file",
                    scope_id=scope_id,
                    provider_id="local_filesystem",
                    title=title,
                    locator=resource_id,
                    capabilities=(),
                    observed_at=NOW,
                )
            )
        dispatcher = server.build_ipc_dispatcher()
        candidates = dispatcher(request_message("c", "relationships.candidates", {}))["result"]["candidates"]
        assert candidates
        denied = dispatcher(
            request_message(
                "a",
                "relationships.admit",
                {"candidate_id": candidates[0]["candidate_id"], "justification": "try before setup"},
            )
        )["result"]
        assert denied == {"ok": False, "error": "only an owner can admit a household relationship"}
        assert server.director.store.events == ()

        server.setup.declare_person(name="Owner", role="owner")
        admitted = dispatcher(
            request_message(
                "b",
                "relationships.admit",
                {"candidate_id": candidates[0]["candidate_id"], "justification": "owner reviewed the edge"},
            )
        )["result"]
        assert admitted["ok"] is True
        assert server.director.store.events[-1].event_type is EventType.RELATIONSHIP_ADMITTED
    finally:
        server.server_close()


def test_scope_fail_closed_edges_and_candidates(stack) -> None:
    tmp, resources, ontology, *_ = stack
    _file(resources, "secret", scope="scope:secret", title="secret alpha beta gamma")
    _file(resources, "visible", title="visible alpha beta gamma")
    projector = RelationshipProjector(resources=resources, ontology=ontology)
    projector.project_all(visible_scopes=("scope:personal",))

    correlator = Correlator(resources=resources, clock=lambda: NOW)
    candidates = correlator.candidates(visible_scopes=("scope:personal",))
    assert all("scope:secret" not in candidate.subject + candidate.object for candidate in candidates)

    service = RelationshipService(
        projector=projector,
        correlator=correlator,
        policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
        ontology=ontology,
        state_path=Path(tmp) / "graph.json",
    )
    secret_edges = service.edges_for("file:secret", visible_scopes=("scope:personal",))
    assert secret_edges["edges"] == []
