"""AutomationRule lifecycle: propose -> approve -> enable/disable -> revoke,
against the same owner-only/proposed-only/cross-household guard conditions
`HavenRuntime.approve_rule`/`revoke_rule` already enforce for device rules --
transcribed here as pure functions rather than through the event-sourced
store, matching the lighter-weight pattern the resource-action vertical
already uses (see `haven/automation/lifecycle.py`'s module docstring)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from haven.automation import (
    ActionTarget,
    AutomationRule,
    AutomationSpec,
    Selector,
    Trigger,
    TriggerKind,
    approve,
    propose,
    revoke,
    set_enabled,
)
from haven.core.consequence import ConsequenceClass
from haven.core.domain import DecisionStatus, Principal, RoleTier, RuleStatus

UTC = timezone.utc
NOW = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)


def _spec(*, household_id: str = "household-a") -> AutomationSpec:
    return AutomationSpec(
        spec_id="spec-1",
        household_id=household_id,
        trigger=Trigger(kind=TriggerKind.TIME, parameters={"time_of_day": "21:00"}),
        selector=Selector(),
        action=ActionTarget(
            domain="computer",
            action="filesystem.move",
            consequence_class=ConsequenceClass.REVERSIBLE_LOCAL,
            parameters={"resource_id": "file-1", "destination": "/archive"},
        ),
        source_text="archive the downloads folder every night at 9pm",
        created_by="gerron",
    )


def _owner(household_id: str = "household-a") -> Principal:
    return Principal(actor_id="owner-1", household_id=household_id, role_tier=RoleTier.OWNER)


def _member(household_id: str = "household-a") -> Principal:
    return Principal(actor_id="member-1", household_id=household_id, role_tier=RoleTier.MEMBER)


def test_propose_starts_proposed_and_enabled():
    rule = propose(_spec(), rule_id="rule-1")
    assert rule.status == RuleStatus.PROPOSED
    assert rule.enabled is True
    assert rule.approved_by is None


def test_an_approved_rule_requires_owner_approval_and_a_timestamp():
    with pytest.raises(ValueError):
        AutomationRule(rule_id="rule-1", spec=_spec(), status=RuleStatus.APPROVED)


def test_a_revoked_rule_requires_a_revoking_actor_and_timestamp():
    with pytest.raises(ValueError):
        AutomationRule(rule_id="rule-1", spec=_spec(), status=RuleStatus.REVOKED)


def test_owner_can_approve_a_proposed_rule():
    rule = propose(_spec(), rule_id="rule-1")
    result = approve(rule, principal=_owner(), justification="trust it", now=NOW)
    assert result.status == DecisionStatus.ALLOW
    assert result.rule.status == RuleStatus.APPROVED
    assert result.rule.approved_by == "owner-1"
    assert result.rule.approved_by_role == RoleTier.OWNER
    assert result.rule.approved_at == NOW


def test_a_member_cannot_approve():
    rule = propose(_spec(), rule_id="rule-1")
    result = approve(rule, principal=_member(), justification="trust it", now=NOW)
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.PROPOSED


def test_approval_requires_a_non_empty_justification():
    rule = propose(_spec(), rule_id="rule-1")
    result = approve(rule, principal=_owner(), justification="   ", now=NOW)
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.PROPOSED


def test_only_a_proposed_rule_can_be_approved():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW).rule
    result = approve(approved, principal=_owner(), justification="again", now=NOW)
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.APPROVED


def test_approval_denies_a_cross_household_principal():
    rule = propose(_spec(household_id="household-a"), rule_id="rule-1")
    result = approve(rule, principal=_owner("household-b"), justification="trust it", now=NOW)
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.PROPOSED


def test_owner_can_revoke_an_approved_rule():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW).rule
    result = revoke(approved, principal=_owner(), justification="no longer needed", now=NOW)
    assert result.status == DecisionStatus.ALLOW
    assert result.rule.status == RuleStatus.REVOKED
    assert result.rule.revoked_by == "owner-1"
    assert result.rule.revoked_at == NOW
    # Revocation preserves the approval record rather than erasing it.
    assert result.rule.approved_by == "owner-1"


def test_a_member_cannot_revoke():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW).rule
    result = revoke(approved, principal=_member(), justification="no longer needed", now=NOW)
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.APPROVED


def test_an_already_revoked_rule_cannot_be_revoked_again():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW).rule
    revoked = revoke(approved, principal=_owner(), justification="no longer needed", now=NOW).rule
    result = revoke(revoked, principal=_owner(), justification="again", now=NOW)
    assert result.status == DecisionStatus.DENY


def test_set_enabled_pauses_without_disturbing_approval_state():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW).rule
    paused = set_enabled(approved, False)
    assert paused.enabled is False
    assert paused.status == RuleStatus.APPROVED
    assert paused.approved_by == approved.approved_by
    resumed = set_enabled(paused, True)
    assert resumed.enabled is True
    assert resumed.status == RuleStatus.APPROVED
