"""Curriculum evaluation: local training-signal generation over a receipt chain.

Contract-only package (see `contracts.py`'s docstring) -- no
`BasicCurriculumEvaluator` implementation yet.
"""

from .contracts import CurriculumEvaluator, CurriculumProposal

__all__ = ["CurriculumEvaluator", "CurriculumProposal"]
