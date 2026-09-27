"""ModelRelationshipProposer: model-derived relationship candidates that
structurally cannot become durable edges on their own (native product-
consolidation plan, P2 "Relationship intelligence": "Add a model proposer
adapter that cannot directly write accepted edges").

`Correlator` (`haven.graph.candidates`) is the deterministic first learner;
this class is the same idea for a model. It holds no reference to
`OntologyStore` or `RelationshipAdmissionPolicy` anywhere -- there is no
method on this class that could write a durable edge even by mistake, the
same "the proposer can't reach the door it isn't given a key to" shape
`haven.intelligence.gateway`'s proposal-only seam already establishes for
rule drafts and chat replies. Every suggestion becomes an ordinary
`CandidateRelationship` and goes through the exact same review pipeline
(`RelationshipAdmissionPolicy.classify()`/`admit()`) a deterministic
candidate does -- `Correlator` and this proposer are two *sources* feeding
one admission boundary, not two authorities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

from .candidates import CandidateRelationship, candidate_id

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

# A model call is expected to return dicts shaped like this -- validated
# defensively, never trusted structurally. Missing `object`/`predicate` or
# an out-of-range confidence drops that one suggestion rather than raising,
# the same "one bad candidate never blanks the batch" posture
# `DiscoveryService.scan()` already applies to a failing transport.
_REQUIRED_KEYS = ("object", "predicate")


class ModelSuggestionSource(Protocol):
    def __call__(self, resource_id: str, *, visible_scopes: tuple[str, ...]) -> list[Mapping[str, object]]:
        """Return raw relationship suggestions for `resource_id`. Never
        called with a scope the caller cannot see; must never be asked to
        (and must never attempt to) write anything -- read-only by
        contract, matching every other intelligence-seam call in this
        repo."""


@dataclass(frozen=True)
class RejectedSuggestion:
    """A raw suggestion this proposer refused to turn into a candidate, and
    why -- surfaced so a caller can tell "the model proposed nothing" apart
    from "the model proposed something malformed," the same distinction
    `haven.discovery`'s honest-gap conventions draw elsewhere in this repo.
    """

    raw: Mapping[str, object]
    reason: str


class ModelRelationshipProposer:
    """Wraps one model call (`propose`) so its output can only ever become
    `CandidateRelationship` objects. `model_id` distinguishes this
    proposer's candidates from the deterministic correlator's in
    `CandidateRelationship.proposed_by`, so a review UI can show provenance
    ("suggested by <model>" vs. "suggested by the deterministic
    correlator").
    """

    def __init__(
        self,
        *,
        model_id: str,
        propose: Callable[[str, tuple[str, ...]], list[Mapping[str, object]]] | ModelSuggestionSource,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        self._model_id = model_id.strip()
        self._propose = propose
        self._clock = clock
        self.last_rejected: tuple[RejectedSuggestion, ...] = ()

    @property
    def proposed_by(self) -> str:
        return f"model:{self._model_id}"

    def candidates_for(
        self,
        resource_id: str,
        *,
        visible_scopes: tuple[str, ...],
        exclude_pairs: frozenset[str] = frozenset(),
    ) -> tuple[CandidateRelationship, ...]:
        try:
            raw_suggestions = self._propose(resource_id, visible_scopes=visible_scopes)
        except Exception:
            # A model call failing must not take the deterministic
            # correlator's own candidates down with it -- the two are
            # independent sources feeding one admission boundary.
            self.last_rejected = ()
            return ()

        now = self._clock()
        candidates: list[CandidateRelationship] = []
        rejected: list[RejectedSuggestion] = []
        for raw in raw_suggestions:
            if not isinstance(raw, Mapping) or any(key not in raw for key in _REQUIRED_KEYS):
                rejected.append(RejectedSuggestion(raw=raw if isinstance(raw, Mapping) else {}, reason="missing required field"))
                continue
            subject = str(raw.get("subject") or resource_id)
            object_ = str(raw["object"])
            predicate = str(raw["predicate"])
            try:
                confidence = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                rejected.append(RejectedSuggestion(raw=raw, reason="confidence is not a number"))
                continue
            if not 0.0 <= confidence <= 1.0:
                rejected.append(RejectedSuggestion(raw=raw, reason="confidence out of range"))
                continue
            scope_id = raw.get("scope_id")
            if not isinstance(scope_id, str) or not scope_id.strip() or scope_id not in visible_scopes:
                rejected.append(RejectedSuggestion(raw=raw, reason="scope_id missing or not visible to this caller"))
                continue
            evidence_refs = raw.get("evidence_refs") or (resource_id,)
            if not isinstance(evidence_refs, (list, tuple)) or not evidence_refs:
                rejected.append(RejectedSuggestion(raw=raw, reason="evidence_refs missing"))
                continue

            pair = candidate_id(subject, object_, predicate)
            if pair in exclude_pairs:
                continue
            candidates.append(
                CandidateRelationship(
                    candidate_id=pair,
                    subject=subject,
                    predicate=predicate,
                    object=object_,
                    evidence_refs=tuple(str(ref) for ref in evidence_refs),
                    confidence=confidence,
                    scope_id=scope_id,
                    proposed_by=self.proposed_by,
                    created_at=now,
                )
            )
        self.last_rejected = tuple(rejected)
        return tuple(candidates)


__all__ = ["ModelRelationshipProposer", "ModelSuggestionSource", "RejectedSuggestion"]
