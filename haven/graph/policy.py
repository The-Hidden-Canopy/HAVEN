"""Relationship admission policy (spec page 21).

Low-impact predicates may be admitted automatically above a confidence
threshold -- they explain, they do not commit. High-impact predicates
(person owns account, task completed, document authoritative, and the rest
of `HIGH_IMPACT_PREDICATES`) require explicit human admission no matter the
confidence: a model may suggest such an edge; it may never silently create
one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from haven.graph.candidates import CandidateRelationship
from haven.knowledge.claims import ClaimState
from haven.ontology.assertions import OntologyAssertion
from haven.ontology.store import OntologyStore

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

# The durable, high-stakes predicates: explicit admission only, always.
HIGH_IMPACT_PREDICATES = frozenset(
    {
        "haven:owned_by",
        "haven:assigned_to",
        "haven:produced",
        "haven:executed_by",
        "haven:visible_to",
        "haven:shared_with",
    }
)

# Explanatory predicates the correlator may propose: auto-admissible.
_LOW_IMPACT_PREDICATES = frozenset({"haven:related_to", "haven:references", "haven:supports"})

_AUTO_ADMIT_CONFIDENCE = 0.6


class RelationshipAdmissionPolicy:
    """Decides what a candidate may become."""

    def __init__(
        self,
        *,
        ontology: OntologyStore,
        clock=_DEFAULT_CLOCK,
        auto_admit_confidence: float = _AUTO_ADMIT_CONFIDENCE,
    ) -> None:
        self._ontology = ontology
        self._clock = clock
        self._auto_admit_confidence = auto_admit_confidence

    def classify(self, candidate: CandidateRelationship) -> str:
        """"auto" | "needs_review" | "rejected" -- the admission verdict."""

        if candidate.predicate in HIGH_IMPACT_PREDICATES:
            return "needs_review"
        if candidate.predicate in _LOW_IMPACT_PREDICATES:
            if candidate.confidence >= self._auto_admit_confidence:
                return "auto"
            return "needs_review"
        return "rejected"

    def admit(self, candidate: CandidateRelationship) -> dict:
        """Explicit admission: an asserted, provenance-carrying edge."""

        assertion = OntologyAssertion(
            assertion_id=f"assert-learned:{candidate.candidate_id.removeprefix('cand:')}",
            subject=candidate.subject,
            predicate=candidate.predicate,
            object=candidate.object,
            scope_id=candidate.scope_id,
            state=ClaimState.REPORTED,
            created_at=self._clock(),
            source_ref=f"learned:{candidate.proposed_by}",
            confidence=candidate.confidence,
        )
        self._ontology.save(assertion)
        return {"ok": True, "assertion_id": assertion.assertion_id}

    def auto_admit(self, candidate: CandidateRelationship) -> dict | None:
        """Admit only when the policy classifies the candidate as auto."""

        if self.classify(candidate) != "auto":
            return None
        return self.admit(candidate)


__all__ = ["HIGH_IMPACT_PREDICATES", "RelationshipAdmissionPolicy"]
