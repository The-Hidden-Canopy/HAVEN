"""UnifiedReceipt (native product-consolidation plan, P1 "Receipts"):
projecting the two mutation-receipt records this repo actually persists --
`ActionReceipt` (home/device) and `ActionLedgerEntry` (resource actions) --
into one field-compatible shape for consumers, without merging either
store's own schema."""

from __future__ import annotations

from datetime import datetime, timezone

from haven.audit.receipts import ActionReceipt
from haven.audit.unified_receipt import UnifiedReceipt, from_action_receipt, from_ledger_entry
from haven.actions.store import ActionLedgerEntry
from haven.core.domain import (
    ActionKind,
    ActionOrigin,
    ActionRequest,
    AuthorityDecision,
    DecisionCode,
    DecisionStatus,
    DeviceResult,
    EvidenceRef,
    EvidenceStatus,
    RoleTier,
)

HOUSEHOLD = "household-1"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _device_receipt(*, with_execution: bool = True, correlation_id: str | None = None) -> ActionReceipt:
    request = ActionRequest(
        request_id="request-1",
        household_id=HOUSEHOLD,
        requested_by="ada",
        rule_id="request-1",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="light.office",
        parameters=(("brightness_pct", 40),),
        justification="turning off the office light",
        evidence_snapshot_id="snapshot-1",
        requested_at=NOW,
        origin=ActionOrigin.DIRECT,
    )
    decision = AuthorityDecision(
        status=DecisionStatus.ALLOW, code=DecisionCode.ALLOWED, explanation="allowed", required_role=RoleTier.MEMBER
    )
    result = DeviceResult(success=True, detail="http_200", observed_at=NOW, source="home_assistant.rest") if with_execution else None
    return ActionReceipt(
        receipt_id="receipt-1",
        requested_action=request,
        interpretation="turning off the office light",
        evidence=(EvidenceRef(kind="presence", subject_id="ada:office", status=EvidenceStatus.OBSERVED, observed_at=NOW, source="demo.house"),),
        decision=decision,
        device_result=result,
        event_ids=("event-1",),
        correlation_id=correlation_id,
    )


def _ledger_entry(*, correlation_id: str | None = "req-corr-1") -> ActionLedgerEntry:
    return ActionLedgerEntry(
        entry_id="ledger-1",
        household_id=HOUSEHOLD,
        provider_id="local_filesystem",
        action="filesystem.move",
        resource_id="resource-1",
        requested_by="ada",
        justification="tidy up",
        parameters=(("source", "/a"), ("destination", "/b")),
        status=DecisionStatus.ALLOW,
        reason="allowed",
        recorded_at=NOW,
        success=True,
        detail="moved",
        correlation_id=correlation_id,
    )


def test_device_receipt_projects_into_the_unified_shape():
    unified = from_action_receipt(_device_receipt())
    assert isinstance(unified, UnifiedReceipt)
    assert unified.receipt_id == "receipt-1"
    assert unified.domain == "home"
    assert unified.actor == "ada"
    assert unified.target == "light.office"
    assert unified.action == "turn_light_off"
    assert unified.authority_status == "allow"
    assert unified.authority_explanation == "allowed"
    assert unified.execution_attempted is True
    assert unified.execution_success is True
    assert unified.execution_detail == "http_200"
    assert unified.correlation_id is None
    assert unified.recorded_at == NOW


def test_device_receipt_projects_its_runtime_correlation_id():
    unified = from_action_receipt(_device_receipt(correlation_id="request-1"))
    assert unified.correlation_id == "request-1"


def test_device_receipt_with_no_execution_reports_not_attempted():
    unified = from_action_receipt(_device_receipt(with_execution=False))
    assert unified.execution_attempted is False
    assert unified.execution_success is None
    assert unified.execution_detail is None


def test_ledger_entry_projects_into_the_same_shape():
    unified = from_ledger_entry(_ledger_entry(), domain="computer")
    assert isinstance(unified, UnifiedReceipt)
    assert unified.receipt_id == "ledger-1"
    assert unified.domain == "computer"
    assert unified.actor == "ada"
    assert unified.provider == "local_filesystem"
    assert unified.target == "resource-1"
    assert unified.action == "filesystem.move"
    assert unified.authority_status == "allow"
    assert unified.authority_explanation == "allowed"
    assert unified.execution_attempted is True
    assert unified.execution_success is True
    assert unified.correlation_id == "req-corr-1"
    assert unified.recorded_at == NOW


def test_both_domains_produce_field_identical_dict_shapes():
    """The actual "one shape, not three" proof: both projections' to_dict()
    have exactly the same key set, regardless of which store they came
    from."""

    device_keys = set(from_action_receipt(_device_receipt()).to_dict())
    ledger_keys = set(from_ledger_entry(_ledger_entry(), domain="browser").to_dict())
    assert device_keys == ledger_keys


def test_to_dict_is_json_shaped():
    payload = from_ledger_entry(_ledger_entry(correlation_id=None), domain="window").to_dict()
    assert payload["correlation_id"] is None
    assert isinstance(payload["recorded_at"], str)
    assert payload["recorded_at"] == NOW.isoformat()
