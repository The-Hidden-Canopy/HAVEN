"""Admission is the boundary between proposals and remembered knowledge."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.knowledge import (
    AdmissionStatus,
    CandidateClaim,
    ClaimAdmissionService,
    ClaimProvenance,
    ClaimState,
    ClaimStore,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _candidate(
    candidate_id: str,
    proposition: str = "the launch date is October 1",
    *,
    provenance: ClaimProvenance = ClaimProvenance.DOCUMENT_STATED,
    source_refs=("file:proposal.md",),
    evidence_refs=("file:proposal.md#line:1",),
    supersedes=(),
    contradicts=(),
) -> CandidateClaim:
    return CandidateClaim(
        candidate_id=candidate_id,
        scope_id="project:haven",
        proposition=proposition,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
        provenance=provenance,
        proposed_confidence=0.9,
        extracted_at=NOW,
        supersedes=supersedes,
        contradicts=contradicts,
    )


def test_document_text_is_reported_not_directly_observed_and_repeated_candidate_dedupes():
    with tempfile.TemporaryDirectory() as tmp:
        admission = ClaimAdmissionService(ClaimStore(Path(tmp) / "claims.db"))
        first = admission.admit(_candidate("candidate-1"))
        second = admission.admit(_candidate("candidate-2"))

        assert first.status is AdmissionStatus.ADMITTED
        assert first.claim.state is ClaimState.REPORTED
        assert first.claim.provenance is ClaimProvenance.DOCUMENT_STATED
        assert second.status is AdmissionStatus.DUPLICATE
        assert len(admission.store.list_all()) == 1


def test_model_inference_without_evidence_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        admission = ClaimAdmissionService(ClaimStore(Path(tmp) / "claims.db"))
        result = admission.admit(
            _candidate(
                "model-1",
                provenance=ClaimProvenance.MODEL_INFERRED,
                evidence_refs=(),
            )
        )

        assert result.status is AdmissionStatus.REJECTED
        assert "evidence" in result.reason
        assert admission.store.list_all() == ()


def test_contradictory_candidates_remain_disputed_without_a_winner():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        admission = ClaimAdmissionService(store)
        first = admission.admit(_candidate("first"))
        second = admission.admit(
            _candidate(
                "second",
                proposition="the launch date is October 2",
                source_refs=("file:notes.md",),
                contradicts=(first.claim.claim_id,),
            )
        )

        assert second.status is AdmissionStatus.ADMITTED
        assert second.claim.state is ClaimState.DISPUTED
        assert store.get(first.claim.claim_id).state is ClaimState.DISPUTED


def test_forget_creates_a_suppression_tombstone_that_survives_rescan():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        admission = ClaimAdmissionService(store)
        first = admission.admit(_candidate("candidate-1"))
        store.forget(first.claim, forgotten_at=NOW, forgotten_by="gerron")

        result = admission.admit(_candidate("candidate-new"))

        assert result.status is AdmissionStatus.SUPPRESSED
        assert store.get(first.claim.claim_id).state is ClaimState.STALE


def test_correction_supersedes_without_rewriting_the_old_claim():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        admission = ClaimAdmissionService(store)
        first = admission.admit(_candidate("candidate-1"))
        correction = admission.correct(
            first.claim,
            proposition="the launch date is October 2",
            actor="gerron",
            now=NOW,
        )

        assert correction.status is AdmissionStatus.ADMITTED
        assert correction.claim.supersedes == (first.claim.claim_id,)
        assert correction.claim.source_refs == first.claim.source_refs
        assert correction.claim.source_refs == ("file:proposal.md",)
        assert all(not ref.startswith("claim:") for ref in correction.claim.source_refs)
        assert correction.claim.evidence_refs == (first.claim.claim_id, "user:gerron")
        assert store.get(first.claim.claim_id).state is ClaimState.STALE
        assert correction.claim.state is ClaimState.REPORTED
