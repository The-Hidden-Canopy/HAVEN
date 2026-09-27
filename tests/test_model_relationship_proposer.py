"""ModelRelationshipProposer (native product-consolidation plan, P2
"Relationship intelligence"): model-derived candidates that structurally
cannot become durable edges, feeding the same admission boundary the
deterministic Correlator already does."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.graph.candidates import CandidateRelationship, Correlator
from haven.graph.deterministic import RelationshipProjector
from haven.graph.model_proposer import ModelRelationshipProposer
from haven.graph.policy import RelationshipAdmissionPolicy
from haven.graph.service import RelationshipService
from haven.ontology.store import OntologyStore
from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
VISIBLE = ("scope:personal",)


def _file(resources: ResourceStore, resource_id: str, *, title: str, scope_id: str = "scope:personal") -> None:
    resources.save(
        ResourceRecord(
            resource_id=resource_id,
            scope_id=scope_id,
            resource_type="file",
            title=title,
            locator=f"/{resource_id}",
            provider_id="fixture",
            capabilities=(),
            observed_at=NOW,
            metadata=(),
        )
    )


def _proposer(propose, resources: ResourceStore, model_id: str = "test-model") -> ModelRelationshipProposer:
    return ModelRelationshipProposer(model_id=model_id, propose=propose, resources=resources, clock=lambda: NOW)


def test_valid_suggestions_become_candidate_relationships():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        _file(resources, "doc-1", title="proposal")

        def propose(resource_id, *, visible_scopes):
            return [
                {
                    "object": "doc-2",
                    "predicate": "haven:related_to",
                    "confidence": 0.8,
                    "scope_id": "scope:personal",
                    "evidence_refs": ["doc-1", "doc-2"],
                }
            ]

        proposer = _proposer(propose, resources)
        candidates = proposer.candidates_for("doc-1", visible_scopes=VISIBLE)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert isinstance(candidate, CandidateRelationship)
        assert candidate.proposed_by == "model:test-model"
        assert candidate.subject == "doc-1"
        assert candidate.object == "doc-2"
        assert candidate.confidence == 0.8
        assert proposer.last_rejected == ()


def test_malformed_suggestions_are_dropped_not_raised():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")

        def propose(resource_id, *, visible_scopes):
            return [
                {"predicate": "haven:related_to"},  # missing object
                {"object": "doc-2", "predicate": "haven:related_to", "confidence": "not-a-number", "scope_id": "scope:personal"},
                {"object": "doc-2", "predicate": "haven:related_to", "confidence": 5.0, "scope_id": "scope:personal"},  # out of range
                {"object": "doc-2", "predicate": "haven:related_to", "confidence": 0.5, "scope_id": "scope:elsewhere"},  # invisible scope
                "not even a mapping",
            ]

        proposer = _proposer(propose, resources)
        candidates = proposer.candidates_for("doc-1", visible_scopes=VISIBLE)

        assert candidates == ()
        assert len(proposer.last_rejected) == 5


def test_a_failing_model_call_produces_no_candidates_and_does_not_raise():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")

        def propose(resource_id, *, visible_scopes):
            raise RuntimeError("model endpoint unreachable")

        proposer = _proposer(propose, resources)
        assert proposer.candidates_for("doc-1", visible_scopes=VISIBLE) == ()


def test_exclude_pairs_are_respected():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")

        def propose(resource_id, *, visible_scopes):
            return [
                {"object": "doc-2", "predicate": "haven:related_to", "confidence": 0.8, "scope_id": "scope:personal"}
            ]

        proposer = _proposer(propose, resources)
        from haven.graph.candidates import candidate_id

        pair = candidate_id("doc-1", "doc-2", "haven:related_to")
        assert proposer.candidates_for("doc-1", visible_scopes=VISIBLE, exclude_pairs=frozenset({pair})) == ()


def test_the_proposer_has_no_way_to_reach_the_ontology_or_admission_policy():
    """A structural assertion: nothing on this class can write a durable
    edge, because it holds no reference to anything that could."""

    proposer = _proposer(lambda *a, **k: [], resources=None)
    assert not hasattr(proposer, "admit")
    assert not hasattr(proposer, "_ontology")
    assert not hasattr(proposer, "_policy")


def _stack():
    tmp = tempfile.mkdtemp()
    resources = ResourceStore(Path(tmp) / "resources.db")
    ontology = OntologyStore(Path(tmp) / "ontology.db")
    return tmp, resources, ontology


def test_relationship_service_merges_correlator_and_model_proposer_candidates():
    tmp, resources, ontology = _stack()
    _file(resources, "alpha", title="alpha bravo charlie")
    _file(resources, "bravo", title="bravo charlie delta echo")  # gives the deterministic correlator something real

    def propose(resource_id, *, visible_scopes):
        if resource_id != "alpha":
            return []
        return [
            {
                "object": "zeta",
                "predicate": "haven:related_to",
                # Below the auto-admit threshold (0.6) on purpose: an
                # auto-admitted candidate is intentionally excluded from
                # this list (it surfaces via edges_for instead), which would
                # make "is it in `candidates()`" the wrong check for a
                # merge -- needs_review is what proves both sources feed
                # the same visible list.
                "confidence": 0.5,
                "scope_id": "scope:personal",
                "evidence_refs": ["alpha", "zeta"],
            }
        ]

    model_proposer = _proposer(propose, resources, model_id="fixture-model")
    service = RelationshipService(
        projector=RelationshipProjector(resources=resources, ontology=ontology),
        correlator=Correlator(resources=resources, clock=lambda: NOW),
        policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
        ontology=ontology,
        state_path=Path(tmp) / "graph.json",
        model_proposers=(model_proposer,),
    )

    result = service.candidates(visible_scopes=VISIBLE)["candidates"]
    sources = {row["proposed_by"] for row in result}
    assert "model:fixture-model" in sources
    assert "haven.correlation" in sources  # the deterministic correlator's candidates are still there too


def test_relationship_service_with_no_model_proposers_behaves_exactly_as_before():
    tmp, resources, ontology = _stack()
    _file(resources, "alpha", title="alpha bravo charlie")
    _file(resources, "bravo", title="bravo charlie delta echo")

    service = RelationshipService(
        projector=RelationshipProjector(resources=resources, ontology=ontology),
        correlator=Correlator(resources=resources, clock=lambda: NOW),
        policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
        ontology=ontology,
        state_path=Path(tmp) / "graph.json",
    )
    result = service.candidates(visible_scopes=VISIBLE)["candidates"]
    assert all(row["proposed_by"] == "haven.correlation" for row in result)


def test_model_proposed_high_impact_candidate_still_requires_explicit_admission():
    """A model suggesting a high-impact predicate must land as
    needs_review, never auto-admitted -- the admission policy, not the
    proposer, decides, and it applies the same rule regardless of source."""

    tmp, resources, ontology = _stack()
    _file(resources, "alpha", title="alpha")

    def propose(resource_id, *, visible_scopes):
        return [
            {
                "object": "person-1",
                "predicate": "haven:owned_by",
                "confidence": 0.99,
                "scope_id": "scope:personal",
                "evidence_refs": ["alpha"],
            }
        ]

    model_proposer = _proposer(propose, resources, model_id="fixture-model")
    service = RelationshipService(
        projector=RelationshipProjector(resources=resources, ontology=ontology),
        correlator=Correlator(resources=resources, clock=lambda: NOW),
        policy=RelationshipAdmissionPolicy(ontology=ontology, clock=lambda: NOW),
        ontology=ontology,
        state_path=Path(tmp) / "graph.json",
        model_proposers=(model_proposer,),
    )
    result = service.candidates(visible_scopes=VISIBLE)["candidates"]
    row = next(r for r in result if r["proposed_by"] == "model:fixture-model")
    assert row["verdict"] == "needs_review"
