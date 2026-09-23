"""`CurriculumEvaluator`: local training-signal generation over a receipt chain.

Contract only -- no `BasicCurriculumEvaluator` implementation yet, the same
split as `haven/trace/contracts.py`: a private evaluator (Ghost Teacher's
adaptive curriculum generation -- probing an intelligence provider,
diagnosing failure modes, and proposing what the next training run should
target) depends on this Protocol and on `haven.audit.receipts.ActionReceipt`;
HAVEN Core never imports the evaluator.

Ghost Teacher is the counterpart to TraceGlass, not a variant of it:
TraceGlass is diagnostic -- it explains what already happened, over a
receipt chain, after the fact. Ghost Teacher is training and improvement --
it looks at the same kind of receipt chain to propose what should change
before the *next* training run. Same receipt source, same "runs locally,
HAVEN Core never imports it" shape, different question being asked of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class CurriculumProposal:
    """An evaluator's own findings -- opaque `signals` payload, the same
    reasoning as `AnalysisResult`: what a curriculum evaluator proposes (a
    failure mode, a capability gap, a training target) is specific to the
    evaluator, not something this shared contract should have to enumerate
    in advance."""

    receipt_refs: tuple[str, ...]
    summary: str
    signals: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_refs", tuple(self.receipt_refs))
        object.__setattr__(self, "summary", _require_text(self.summary, name="summary"))
        object.__setattr__(self, "signals", tuple(self.signals))


class CurriculumEvaluator(Protocol):
    def evaluate(self, receipt_refs: tuple[str, ...]) -> CurriculumProposal: ...


__all__ = ["CurriculumEvaluator", "CurriculumProposal"]
