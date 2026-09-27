"""KnowledgeSource: disputed claims (spec 6, "material claim conflict").

`ClaimState.DISPUTED` already exists as an honest first-class state
(`haven/knowledge/claims.py`) for two trusted sources disagreeing -- this
adapter surfaces it as a Needs You conflict rather than adding a second
disagreement concept.
"""

from __future__ import annotations

from datetime import datetime

from ...knowledge.claims import ClaimState
from ..domain import AttentionActionRef, AttentionItem, AttentionKind, AttentionSeverity, Dismissibility, RouteRef

_TITLE_LIMIT = 80


class KnowledgeSource:
    def __init__(self, *, knowledge, identity) -> None:
        self._knowledge = knowledge
        self._identity = identity

    def collect(self, *, now: datetime, visible_scope_ids: tuple[str, ...]) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for claim in self._knowledge.list_claims(scope_ids=visible_scope_ids, include_stale=False):
            if claim.state != ClaimState.DISPUTED:
                continue
            title = claim.proposition
            if len(title) > _TITLE_LIMIT:
                title = title[: _TITLE_LIMIT - 1].rstrip() + "…"
            why_now = "Two or more sources disagree about this claim; review the evidence before treating it as settled."
            items.append(
                AttentionItem(
                    attention_id=f"conflict:claim:{claim.claim_id}",
                    kind=AttentionKind.CONFLICT,
                    severity=AttentionSeverity.NORMAL,
                    title=title,
                    summary=why_now,
                    why_now=why_now,
                    source_domain="memory",
                    source_ref=f"claim:{claim.claim_id}",
                    scope_id=claim.scope_id,
                    created_at=claim.created_at,
                    evidence_refs=claim.evidence_refs or (claim.claim_id,),
                    route=RouteRef(page="memory", action_hint="review", entity_id=claim.claim_id, anchor="conflict"),
                    available_actions=(AttentionActionRef(action="review", label="Review evidence"),),
                    dismissibility=Dismissibility.DISMISS,
                )
            )
        return items


__all__ = ["KnowledgeSource"]
