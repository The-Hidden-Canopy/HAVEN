"""Learned-relationship candidates: shared-token correlation (spec page 21).

The first learner is deliberately deterministic-ish: significant tokens
shared across resource titles/content inside the visible scopes become
`CandidateRelationship`s proposing `related_to` -- a low-impact predicate.
Evidence refs name both resources; confidence is the overlap ratio. A
future model proposes through the same candidate contract; the admission
policy (not the proposer) decides what becomes durable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from haven.ontology.predicates import RELATED_TO
from haven.resources.store import ResourceStore

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

_TOKEN = re.compile(r"[a-z0-9]{4,}")
_STOPWORDS = frozenset(
    {
        "this", "that", "with", "from", "have", "will", "your", "about", "document",
        "file", "note", "notes", "draft", "final", "proposal", "review", "the", "and",
    }
)
_MAX_CANDIDATES_PER_RUN = 50
_MIN_CONFIDENCE = 0.34


def candidate_id(subject: str, object_: str, predicate: str) -> str:
    digest = hashlib.sha256(f"{subject}|{predicate}|{object_}".encode("utf-8")).hexdigest()[:16]
    return f"cand:{digest}"


@dataclass(frozen=True)
class CandidateRelationship:
    candidate_id: str
    subject: str
    predicate: str
    object: str
    evidence_refs: tuple[str, ...]
    confidence: float
    scope_id: str
    proposed_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    def wire(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "scope_id": self.scope_id,
            "proposed_by": self.proposed_by,
            "created_at": self.created_at.isoformat(),
        }


def significant_tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in _TOKEN.findall(text.lower()) if token not in _STOPWORDS)


class Correlator:
    """Produces related_to candidates from shared significant tokens."""

    proposed_by = "haven.correlation"

    def __init__(self, *, resources: ResourceStore, clock=_DEFAULT_CLOCK) -> None:
        self._resources = resources
        self._clock = clock

    def candidates_for(
        self,
        resource_id: str,
        *,
        visible_scopes: tuple[str, ...],
        exclude_pairs: frozenset[str] = frozenset(),
        limit: int = _MAX_CANDIDATES_PER_RUN,
    ) -> tuple[CandidateRelationship, ...]:
        record = self._resources.get(resource_id)
        if record is None or record.stale:
            return ()
        tokens = significant_tokens(record.title + " " + " ".join(str(v) for _, v in record.metadata))
        if not tokens:
            return ()
        found: list[CandidateRelationship] = []
        seen_scopes = set()
        for scope_id in visible_scopes:
            if scope_id in seen_scopes:
                continue
            seen_scopes.add(scope_id)
            for other in self._resources.list_by_scope(scope_id):
                if other.resource_id == resource_id or other.stale:
                    continue
                pair = candidate_id(resource_id, other.resource_id, RELATED_TO)
                if pair in exclude_pairs:
                    continue
                other_tokens = significant_tokens(
                    other.title + " " + " ".join(str(v) for _, v in other.metadata)
                )
                shared = tokens & other_tokens
                if not shared:
                    continue
                confidence = round(len(shared) / max(len(tokens | other_tokens), 1), 3)
                if confidence < _MIN_CONFIDENCE:
                    continue
                found.append(
                    CandidateRelationship(
                        candidate_id=pair,
                        subject=resource_id,
                        predicate=RELATED_TO,
                        object=other.resource_id,
                        evidence_refs=(resource_id, other.resource_id),
                        confidence=confidence,
                        scope_id=record.scope_id,
                        proposed_by=self.proposed_by,
                        created_at=self._clock(),
                    )
                )
        found.sort(key=lambda item: (-item.confidence, item.object))
        return tuple(found[:limit])

    def candidates(
        self, *, visible_scopes: tuple[str, ...], exclude_pairs: frozenset[str] = frozenset()
    ) -> tuple[CandidateRelationship, ...]:
        all_candidates: dict[str, CandidateRelationship] = {}
        seen_scopes = set()
        for scope_id in visible_scopes:
            if scope_id in seen_scopes:
                continue
            seen_scopes.add(scope_id)
            for record in self._resources.list_by_scope(scope_id):
                if record.stale:
                    continue
                for candidate in self.candidates_for(
                    record.resource_id,
                    visible_scopes=(scope_id,),
                    exclude_pairs=exclude_pairs,
                ):
                    all_candidates.setdefault(candidate.candidate_id, candidate)
                    if len(all_candidates) >= _MAX_CANDIDATES_PER_RUN:
                        return tuple(sorted(
                            all_candidates.values(),
                            key=lambda item: (-item.confidence, item.candidate_id),
                        ))
        return tuple(sorted(
            all_candidates.values(),
            key=lambda item: (-item.confidence, item.candidate_id),
        ))


__all__ = ["CandidateRelationship", "Correlator", "candidate_id", "significant_tokens"]
