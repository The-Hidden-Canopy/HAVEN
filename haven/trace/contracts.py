"""`TraceAnalyzer`: deeper analysis over a receipt chain, layered on top.

Contract only -- no `BasicTraceAnalyzer` implementation yet. A private
analyzer (TraceGlass's available-evidence -> belief -> candidate-action ->
executed-action -> consequence reconstruction) depends on this Protocol and
on `haven.audit.receipts.ActionReceipt`; HAVEN Core never imports the
analyzer. The receipt chain itself (`haven/audit/receipts.py`) is unchanged
and stays public regardless of which analyzer, if any, a household runs
over it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class AnalysisResult:
    """An analyzer's own findings -- opaque `findings` payload, because
    what a deep analyzer produces (a causal breakpoint, a training signal,
    a compliance flag) is specific to the analyzer, not something this
    shared contract should have to enumerate in advance."""

    receipt_refs: tuple[str, ...]
    summary: str
    findings: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_refs", tuple(self.receipt_refs))
        object.__setattr__(self, "summary", _require_text(self.summary, name="summary"))
        object.__setattr__(self, "findings", tuple(self.findings))


class TraceAnalyzer(Protocol):
    def analyze(self, receipt_refs: tuple[str, ...]) -> AnalysisResult: ...


__all__ = ["AnalysisResult", "TraceAnalyzer"]
