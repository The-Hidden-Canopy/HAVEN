"""Needs You: a cross-domain human-attention projection.

Spec: `HAVEN_Needs_You_Temporal_Home_Spec_REFRESHED.docx`, sections 3-10.
Needs You is narrower than "attention" -- an item belongs here only when
HAVEN can state the eligibility test (spec 3.1): without a human response
the condition stays unresolved, blocked, unsafe to advance, or materially
ambiguous. The service aggregates evidence from narrow per-domain source
adapters; it never mutates a domain on its own (spec 4.1).
"""

from .domain import (
    AttentionActionRef,
    AttentionItem,
    AttentionKind,
    AttentionSeverity,
    AttentionStatus,
    Dismissibility,
    RouteRef,
    attention_item_from_projection,
)
from .service import NeedsYouService

__all__ = [
    "AttentionActionRef",
    "AttentionItem",
    "AttentionKind",
    "AttentionSeverity",
    "AttentionStatus",
    "Dismissibility",
    "NeedsYouService",
    "RouteRef",
    "attention_item_from_projection",
]
