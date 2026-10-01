"""UnifiedReceipt: one output shape for consumers, across the two mutation-
receipt records this repo actually persists (native product-consolidation
plan, P1 "Receipts" — "standardize mutation receipt fields (actor, target,
provider, authority decision, consequence evidence) across device,
resource, and communication actions — one shape, not three").

`ActionReceipt` (`haven.audit.receipts`, home/device actions, persisted in
`HistoryStore`) and `ActionLedgerEntry` (`haven.actions.store`, resource
actions — computer/browser/window today — persisted in
`ActionLedgerStore`) are two different records for reasons this pass does
not undo: they predate each other, serve different stores, and a
communications mutation path does not exist yet to even need a third. This
module does not merge those stores or their schemas — that is a much larger
migration than "standardize the fields a viewer sees," and nothing in the
plan's own sequencing (WP1 is proof-loop/docs work, not a store migration)
asks for it here. What it gives consumers (a diagnostic export, a future
combined receipts page, a support bundle) is the "one shape" itself: every
receipt, regardless of which store it came from, converts into one
`UnifiedReceipt` with the same field names.

`ActionReceipt` now retains the same request-scoped correlation value used
by the runtime's transition events. Direct actions use their request id;
rule actions use their rule correlation, so the projection does not invent a
second correlation scope or overwrite the event timeline's value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..actions.store import ActionLedgerEntry
from .receipts import ActionReceipt


@dataclass(frozen=True)
class UnifiedReceipt:
    receipt_id: str
    domain: str
    actor: str
    provider: str | None
    target: str | None
    action: str
    justification: str | None
    authority_status: str
    authority_explanation: str | None
    execution_attempted: bool
    execution_success: bool | None
    execution_detail: str | None
    correlation_id: str | None
    recorded_at: datetime

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "domain": self.domain,
            "actor": self.actor,
            "provider": self.provider,
            "target": self.target,
            "action": self.action,
            "justification": self.justification,
            "authority_status": self.authority_status,
            "authority_explanation": self.authority_explanation,
            "execution_attempted": self.execution_attempted,
            "execution_success": self.execution_success,
            "execution_detail": self.execution_detail,
            "correlation_id": self.correlation_id,
            "recorded_at": self.recorded_at.isoformat(),
        }


def from_action_receipt(receipt: ActionReceipt) -> UnifiedReceipt:
    """Project a home/device `ActionReceipt` into the unified shape."""

    request = receipt.requested_action
    decision = receipt.decision
    execution = receipt.device_result
    return UnifiedReceipt(
        receipt_id=receipt.receipt_id,
        domain="home",
        actor=request.requested_by,
        provider=None,  # device actions execute through the household's device registry, not a named provider_id
        target=request.target_device_id,
        action=request.action_kind.value,
        justification=request.justification,
        authority_status=decision.status.value,
        authority_explanation=decision.explanation,
        execution_attempted=execution is not None,
        execution_success=execution.success if execution is not None else None,
        execution_detail=execution.detail if execution is not None else None,
        correlation_id=receipt.correlation_id,
        recorded_at=request.requested_at,
    )


def from_ledger_entry(entry: ActionLedgerEntry, *, domain: str) -> UnifiedReceipt:
    """Project a resource-action `ActionLedgerEntry` (computer, browser,
    window today) into the unified shape. `domain` names which of those --
    the entry itself does not distinguish them, only its caller knows."""

    return UnifiedReceipt(
        receipt_id=entry.entry_id,
        domain=domain,
        actor=entry.requested_by,
        provider=entry.provider_id,
        target=entry.resource_id,
        action=entry.action,
        justification=entry.justification or None,
        authority_status=entry.status.value,
        authority_explanation=entry.reason,
        execution_attempted=entry.success is not None,
        execution_success=entry.success,
        execution_detail=entry.detail,
        correlation_id=entry.correlation_id,
        recorded_at=entry.recorded_at,
    )


__all__ = ["UnifiedReceipt", "from_action_receipt", "from_ledger_entry"]
