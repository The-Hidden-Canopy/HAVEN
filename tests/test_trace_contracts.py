"""haven/trace: TraceGlass's public contract, no implementation in this repo.

`TraceAnalyzer` is a local Protocol over `haven.audit.receipts.ActionReceipt`
-- HAVEN Core never imports an implementation, and the actual analyzer is a
private product. This is the same shape as a `haven/providers` contract,
applied to deeper receipt-chain analysis instead of a runtime capability.
These tests only cover the shared `AnalysisResult` dataclass -- the
Protocol itself is structural and has nothing to unit test until a concrete
implementation exists.
"""

from __future__ import annotations

import pytest

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


def test_trace_analyzer_is_a_structural_protocol():
    class FakeAnalyzer:
        def analyze(self, receipt_refs: tuple[str, ...]) -> AnalysisResult:
            return AnalysisResult(receipt_refs=receipt_refs, summary="ok")

    analyzer: TraceAnalyzer = FakeAnalyzer()
    result = analyzer.analyze(("r1",))
    assert result.receipt_refs == ("r1",)
