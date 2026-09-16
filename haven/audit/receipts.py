"""Machine-readable receipts for the governed action chain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from typing import Any

from haven.core.domain import (
    ActionRequest,
    AuthorityDecision,
    DecisionStatus,
    DeviceResult,
    EvidenceRef,
)


RECEIPT_SCHEMA_VERSION = "0.1.0"


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    requested_action: ActionRequest
    interpretation: str
    evidence: tuple[EvidenceRef, ...]
    decision: AuthorityDecision
    device_result: DeviceResult | None
    event_ids: tuple[str, ...]

    @property
    def execution_attempted(self) -> bool:
        return self.device_result is not None

    @property
    def outcome(self) -> str:
        if self.decision.status != DecisionStatus.ALLOW:
            return self.decision.status.value
        if self.device_result is None:
            return "execution_not_recorded"
        return "executed" if self.device_result.success else "execution_failed"

    def to_dict(self) -> dict[str, Any]:
        """Export a trace-friendly receipt without serializing confirmation data."""

        request = self.requested_action
        authority = self.decision
        execution = None
        if self.device_result is not None:
            execution = {
                "attempted": True,
                "success": self.device_result.success,
                "detail": self.device_result.detail,
                "observed_at": self.device_result.observed_at.isoformat(),
                "source": self.device_result.source,
            }
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "requested_action": {
                "request_id": request.request_id,
                "household_id": request.household_id,
                "requested_by": request.requested_by,
                "rule_id": request.rule_id,
                "action_kind": request.action_kind.value,
                "target_device_id": request.target_device_id,
                "parameters": {key: _json_value(value) for key, value in request.parameters},
                "justification": request.justification,
                "evidence_snapshot_id": request.evidence_snapshot_id,
                "requested_at": request.requested_at.isoformat(),
                "confirmation_present": request.confirmation_token is not None,
            },
            "interpretation": self.interpretation,
            "evidence": [
                {
                    "kind": item.kind,
                    "subject_id": item.subject_id,
                    "status": item.status.value,
                    "observed_at": item.observed_at.isoformat(),
                    "source": item.source,
                }
                for item in self.evidence
            ],
            "authority_decision": {
                "status": authority.status.value,
                "code": authority.code.value,
                "explanation": authority.explanation,
                "required_role": authority.required_role.name if authority.required_role is not None else None,
            },
            "execution": execution,
            "event_ids": list(self.event_ids),
            "outcome": self.outcome,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)


__all__ = ["ActionReceipt", "RECEIPT_SCHEMA_VERSION"]
