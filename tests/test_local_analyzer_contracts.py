"""haven/trace and haven/curriculum: public contracts, private implementations.

TraceGlass (diagnostic, haven.trace.TraceAnalyzer) and Ghost Teacher
(training and improvement, haven.curriculum.CurriculumEvaluator) are the
same shape of split: a Protocol lives here, HAVEN Core never imports an
implementation, and the actual analyzer/evaluator is a private product.
These tests only cover the shared result dataclasses -- the Protocols
themselves are structural and have nothing to unit test until a concrete
implementation exists.
"""

from __future__ import annotations

import pytest

from haven.curriculum import CurriculumEvaluator, CurriculumProposal
from haven.trace import AnalysisResult, TraceAnalyzer


def test_analysis_result_normalizes_refs_and_findings_to_tuples():
    result = AnalysisResult(
        receipt_refs=["r1", "r2"],
        summary="the chain broke at evidence staleness",
        findings=[("kind", "stale_evidence")],
    )
    assert result.receipt_refs == ("r1", "r2")
    assert result.findings == (("kind", "stale_evidence"),)


def test_analysis_result_rejects_a_blank_summary():
    with pytest.raises(ValueError):
        AnalysisResult(receipt_refs=(), summary="   ")


def test_curriculum_proposal_normalizes_refs_and_signals_to_tuples():
    proposal = CurriculumProposal(
        receipt_refs=["r1"],
        summary="the intelligence provider under-performed on ambiguous scope",
        signals=[("failure_mode", "ambiguous_scope")],
    )
    assert proposal.receipt_refs == ("r1",)
    assert proposal.signals == (("failure_mode", "ambiguous_scope"),)


def test_curriculum_proposal_rejects_a_blank_summary():
    with pytest.raises(ValueError):
        CurriculumProposal(receipt_refs=(), summary="")


def test_trace_analyzer_and_curriculum_evaluator_are_distinct_protocols():
    # Same shape (one method over receipt_refs), different question asked of
    # it -- confirmed by them being genuinely different Protocol objects,
    # not one aliased to the other.
    assert TraceAnalyzer is not CurriculumEvaluator
    assert AnalysisResult is not CurriculumProposal
