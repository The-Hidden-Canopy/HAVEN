"""Proposals that may become durable HAVEN knowledge.

An extractor produces a ``CandidateClaim``; it never chooses a ``ClaimState``
and never writes the claim store. That boundary keeps document text and model
inferences from masquerading as direct observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from haven.core.time import require_aware_utc

from .claims import ClaimProvenance
from .store import fingerprint_for_values


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class CandidateClaim:
    """A sourced proposition awaiting the admission policy."""

    candidate_id: str
    scope_id: str
    proposition: str
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    provenance: ClaimProvenance
    proposed_confidence: float
    extracted_at: datetime
    supersedes: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, name="candidate_id"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "proposition", _require_text(self.proposition, name="proposition"))
        object.__setattr__(self, "source_refs", tuple(ref for ref in self.source_refs if str(ref).strip()))
        object.__setattr__(self, "evidence_refs", tuple(ref for ref in self.evidence_refs if str(ref).strip()))
        if not isinstance(self.provenance, ClaimProvenance):
            raise ValueError("provenance must be a ClaimProvenance")
        if not 0.0 <= self.proposed_confidence <= 1.0:
            raise ValueError("proposed_confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "extracted_at", require_aware_utc(self.extracted_at, name="extracted_at"))
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        object.__setattr__(self, "contradicts", tuple(self.contradicts))

    @property
    def fingerprint(self) -> str:
        return fingerprint_for_values(
            scope_id=self.scope_id,
            proposition=self.proposition,
            provenance=self.provenance,
            source_refs=self.source_refs,
        )


__all__ = ["CandidateClaim"]
