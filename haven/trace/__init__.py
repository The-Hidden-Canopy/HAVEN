"""Trace analysis: deeper findings layered over a receipt chain.

Contract-only package (see `contracts.py`'s docstring) -- no
`BasicTraceAnalyzer` implementation yet.
"""

from .contracts import AnalysisResult, TraceAnalyzer

__all__ = ["AnalysisResult", "TraceAnalyzer"]
