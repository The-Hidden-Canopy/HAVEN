"""Decision review projection into existing Needs You; no approval path."""

from haven.attention.domain import AttentionActionRef, AttentionItem, AttentionKind, AttentionSeverity, Dismissibility, RouteRef
from datetime import datetime


class DecisionSource:
    def __init__(self, *, decisions):
        self.decisions = decisions

    def collect(self, *, now, visible_scope_ids):
        items = []
        for scope_id in visible_scope_ids:
            try:
                records = self.decisions.review_items(scope_id)
            except PermissionError:
                continue
            for record, issues in records:
                items.append(AttentionItem(
                    attention_id=f"decision:{record.decision_id}", kind=AttentionKind.AMBIGUITY,
                    severity=AttentionSeverity.NORMAL, title=record.current.problem[:80],
                    summary="; ".join(issues), why_now="Review the decision and reconcile its evidence and documents.",
                    source_domain="memory", source_ref=record.decision_id, scope_id=scope_id,
                    created_at=datetime.fromisoformat(record.current.recorded_at),
                    evidence_refs=tuple(ref.resource_id for ref in record.current.evidence),
                    route=RouteRef(page="memory", subview="decisions", entity_id=record.decision_id, action_hint="review"),
                    available_actions=(AttentionActionRef(action="review", label="Review decision"),),
                    dismissibility=Dismissibility.SNOOZE))
        return items
