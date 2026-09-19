"""The only normal path from a candidate proposition to a remembered claim."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum

from .candidates import CandidateClaim
from .claims import Claim, ClaimProvenance, ClaimState
from .store import ClaimStore


class AdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    DUPLICATE = "duplicate"
    SUPPRESSED = "suppressed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AdmissionResult:
    status: AdmissionStatus
    claim: Claim | None = None
    reason: str | None = None


class ClaimAdmissionPolicy:
    """Validate the evidence boundary without trying to prove semantics."""

    def validate(self, candidate: CandidateClaim) -> str | None:
        if not candidate.source_refs:
            return "a candidate must identify at least one source"
        if candidate.provenance is ClaimProvenance.MODEL_INFERRED and not candidate.evidence_refs:
            return "a model-inferred candidate must include evidence references"
        if candidate.provenance is ClaimProvenance.DIRECT_OBSERVATION and not candidate.evidence_refs:
            return "a direct observation must include evidence references"
        return None


class ClaimAdmissionService:
    """Map provenance to state, deduplicate, and persist admitted claims."""

    def __init__(self, store: ClaimStore, *, policy: ClaimAdmissionPolicy | None = None) -> None:
        self._store = store
        self._policy = policy or ClaimAdmissionPolicy()

    @property
    def store(self) -> ClaimStore:
        return self._store

    def admit(self, candidate: CandidateClaim) -> AdmissionResult:
        reason = self._policy.validate(candidate)
        if reason is not None:
            return AdmissionResult(AdmissionStatus.REJECTED, reason=reason)
        if self._store.is_forgotten(candidate.fingerprint):
            return AdmissionResult(AdmissionStatus.SUPPRESSED, reason="candidate was deliberately forgotten")
        existing = self._store.find_by_fingerprint(candidate.fingerprint)
        if existing is not None:
            return AdmissionResult(AdmissionStatus.DUPLICATE, claim=existing, reason="already admitted")

        state = {
            ClaimProvenance.DIRECT_OBSERVATION: ClaimState.OBSERVED,
            ClaimProvenance.USER_REPORTED: ClaimState.REPORTED,
            ClaimProvenance.DOCUMENT_STATED: ClaimState.REPORTED,
            ClaimProvenance.MODEL_INFERRED: ClaimState.INFERRED,
        }[candidate.provenance]
        if len(candidate.source_refs) > 1:
            state = ClaimState.CORROBORATED
        if candidate.contradicts:
            state = ClaimState.DISPUTED
            for claim_id in candidate.contradicts:
                prior = self._store.get(claim_id)
                if prior is not None and prior.state is not ClaimState.DISPUTED:
                    self._store.save(replace(prior, state=ClaimState.DISPUTED))
        for claim_id in candidate.supersedes:
            self._store.mark_stale(claim_id)

        digest = hashlib.sha256(
            f"{candidate.candidate_id}\x1e{candidate.fingerprint}".encode("utf-8")
        ).hexdigest()[:32]
        claim = Claim(
            claim_id=f"claim:{digest}",
            scope_id=candidate.scope_id,
            proposition=candidate.proposition,
            state=state,
            source_refs=candidate.source_refs,
            evidence_refs=candidate.evidence_refs,
            created_at=candidate.extracted_at,
            confidence=candidate.proposed_confidence,
            provenance=candidate.provenance,
            supersedes=candidate.supersedes,
            contradicts=candidate.contradicts,
        )
        self._store.save(claim)
        return AdmissionResult(AdmissionStatus.ADMITTED, claim=claim)

    def correct(
        self,
        prior: Claim,
        *,
        proposition: str,
        actor: str,
        now: datetime | None = None,
    ) -> AdmissionResult:
        if not isinstance(actor, str) or not actor.strip():
            return AdmissionResult(AdmissionStatus.REJECTED, reason="a correction actor is required")
        now = now or datetime.now(timezone.utc)
        candidate = CandidateClaim(
            candidate_id=f"correction:{prior.claim_id}:{now.isoformat()}",
            scope_id=prior.scope_id,
            proposition=proposition,
            # A correction remains about the same underlying sources.  The
            # prior claim is already represented in `supersedes` and the
            # actor/evidence trail below; wrapping its id as a resource-like
            # source would produce values such as `claim:claim:abc` and make
            # the corrected claim impossible to resolve back to its source.
            source_refs=prior.source_refs,
            evidence_refs=(prior.claim_id, f"user:{actor.strip()}"),
            provenance=ClaimProvenance.USER_REPORTED,
            proposed_confidence=1.0,
            extracted_at=now,
            supersedes=(prior.claim_id,),
        )
        return self.admit(candidate)


__all__ = [
    "AdmissionResult",
    "AdmissionStatus",
    "ClaimAdmissionPolicy",
    "ClaimAdmissionService",
]
