"""An allowlisted local receipt projection. There is no relay in this module."""

from __future__ import annotations

from typing import Callable

from ..audit.receipts import ActionReceipt


def authorized_receipt_projection(receipt: ActionReceipt, *, principal_id: str, scope_id: str,
                                  consumer_id: str, authorize: Callable[[str, str, str], bool]) -> dict:
    """Require fresh, explicit export authority before constructing a projection.

    Consumer enablement and product installation are not export authorization.
    Even authorized output excludes IDs, free text, parameters, timestamps,
    evidence, device names, capability strings, and external source material.
    """
    if not isinstance(receipt, ActionReceipt):
        raise ValueError("raw dictionaries are not exportable receipts")
    if scope_id != receipt.requested_action.household_id:
        raise PermissionError("receipt is outside the requested export scope")
    if not all(isinstance(x, str) and x.strip() for x in (principal_id, scope_id, consumer_id)):
        raise PermissionError("export requires an actor, scope and consumer")
    try:
        allowed = authorize(principal_id, scope_id, consumer_id) is True
    except Exception:
        allowed = False
    if not allowed:
        raise PermissionError("explicit current export authorization is required")
    return {"schema": "haven-redacted-receipt.v1", "outcome": receipt.outcome,
            "authority_status": receipt.decision.status.value,
            "execution_attempted": receipt.execution_attempted,
            "execution_success": None if receipt.device_result is None else bool(receipt.device_result.success)}
