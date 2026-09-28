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
    AutomationLifecycleEvent,
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


def _audit(events: list[AutomationLifecycleEvent] | None = None):
    captured = events if events is not None else []
    return captured.append


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
    events: list[AutomationLifecycleEvent] = []
    result = approve(rule, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit(events))
    assert result.status == DecisionStatus.ALLOW
    assert result.rule.status == RuleStatus.APPROVED
    assert result.rule.approved_by == "owner-1"
    assert result.rule.approved_by_role == RoleTier.OWNER
    assert result.rule.approved_at == NOW
    assert events[0].transition == "approve"
    assert events[0].status == DecisionStatus.ALLOW
    assert events[0].justification == "trust it"


def test_lifecycle_event_rejects_unknown_transitions_and_blank_justification():
    fields = dict(
        event_id="event-1",
        transition="approve",
        household_id="household-a",
        rule_id="rule-1",
        actor_id="owner-1",
        status=DecisionStatus.ALLOW,
        enabled=True,
        justification="approved for the evening",
        occurred_at=NOW,
        correlation_id="request-1",
    )
    with pytest.raises(ValueError, match="transition"):
        AutomationLifecycleEvent(**{**fields, "transition": "invented"})
    with pytest.raises(ValueError, match="justification"):
        AutomationLifecycleEvent(**{**fields, "justification": "   "})


def test_a_member_cannot_approve():
    rule = propose(_spec(), rule_id="rule-1")
    result = approve(rule, principal=_member(), justification="trust it", now=NOW, event_sink=_audit())
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.PROPOSED


def test_approval_requires_a_non_empty_justification():
    rule = propose(_spec(), rule_id="rule-1")
    events: list[AutomationLifecycleEvent] = []
    result = approve(rule, principal=_owner(), justification="   ", now=NOW, event_sink=_audit(events))
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.PROPOSED
    assert events[0].status == DecisionStatus.DENY
    assert events[0].justification == "[missing justification]"


def test_only_a_proposed_rule_can_be_approved():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule
    result = approve(approved, principal=_owner(), justification="again", now=NOW, event_sink=_audit())
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.APPROVED


def test_approval_denies_a_cross_household_principal():
    rule = propose(_spec(household_id="household-a"), rule_id="rule-1")
    result = approve(rule, principal=_owner("household-b"), justification="trust it", now=NOW, event_sink=_audit())
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.PROPOSED


def test_owner_can_revoke_an_approved_rule():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule
    events: list[AutomationLifecycleEvent] = []
    result = revoke(
        approved,
        principal=_owner(),
        justification="no longer needed",
        now=NOW,
        event_sink=_audit(events),
    )
    assert result.status == DecisionStatus.ALLOW
    assert result.rule.status == RuleStatus.REVOKED
    assert result.rule.revoked_by == "owner-1"
    assert result.rule.revoked_at == NOW
    # Revocation preserves the approval record rather than erasing it.
    assert result.rule.approved_by == "owner-1"
    assert events[0].transition == "revoke"


def test_separate_transitions_receive_distinct_generated_correlations():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(
        rule,
        principal=_owner(),
        justification="trust it",
        now=NOW,
        event_sink=_audit(),
    )
    revoked = revoke(
        approved.rule,
        principal=_owner(),
        justification="no longer needed",
        now=NOW,
        event_sink=_audit(),
    )

    assert approved.event.correlation_id.startswith("automation-correlation-")
    assert revoked.event.correlation_id.startswith("automation-correlation-")
    assert approved.event.correlation_id != revoked.event.correlation_id


def test_a_member_cannot_revoke():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule
    result = revoke(approved, principal=_member(), justification="no longer needed", now=NOW, event_sink=_audit())
    assert result.status == DecisionStatus.DENY
    assert result.rule.status == RuleStatus.APPROVED


def test_an_already_revoked_rule_cannot_be_revoked_again():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule
    revoked = revoke(
        approved,
        principal=_owner(),
        justification="no longer needed",
        now=NOW,
        event_sink=_audit(),
    ).rule
    result = revoke(revoked, principal=_owner(), justification="again", now=NOW, event_sink=_audit())
    assert result.status == DecisionStatus.DENY


def test_set_enabled_pauses_without_disturbing_approval_state():
    rule = propose(_spec(), rule_id="rule-1")
    approved = approve(rule, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule
    events: list[AutomationLifecycleEvent] = []
    paused = set_enabled(
        approved,
        False,
        principal=_owner(),
        justification="pause while away",
        now=NOW,
        event_sink=_audit(events),
    ).rule
    assert paused.enabled is False
    assert paused.status == RuleStatus.APPROVED
    assert paused.approved_by == approved.approved_by
    resumed = set_enabled(
        paused,
        True,
        principal=_owner(),
        justification="resume for the evening",
        now=NOW,
        event_sink=_audit(),
    ).rule
    assert resumed.enabled is True
    assert resumed.status == RuleStatus.APPROVED
    assert [event.transition for event in events] == ["set_enabled"]


def test_enablement_denies_cross_household_member_and_missing_justification():
    approved = approve(propose(_spec(), rule_id="rule-1"), principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule

    foreign = set_enabled(
        approved,
        False,
        principal=_owner("household-b"),
        justification="pause",
        now=NOW,
        event_sink=_audit(),
    )
    member = set_enabled(
        approved,
        False,
        principal=_member(),
        justification="pause",
        now=NOW,
        event_sink=_audit(),
    )
    missing = set_enabled(
        approved,
        False,
        principal=_owner(),
        justification="   ",
        now=NOW,
        event_sink=_audit(),
    )

    assert foreign.status == DecisionStatus.DENY
    assert member.status == DecisionStatus.DENY
    assert missing.status == DecisionStatus.DENY
    assert foreign.rule == approved
    assert member.rule == approved
    assert missing.rule == approved


def test_enablement_rejects_proposed_revoked_and_naive_transitions():
    proposed = propose(_spec(), rule_id="rule-1")
    proposed_result = set_enabled(
        proposed,
        False,
        principal=_owner(),
        justification="pause",
        now=NOW,
        event_sink=_audit(),
    )
    approved = approve(proposed, principal=_owner(), justification="trust it", now=NOW, event_sink=_audit()).rule
    revoked = revoke(
        approved,
        principal=_owner(),
        justification="stop",
        now=NOW,
        event_sink=_audit(),
    ).rule
    revoked_result = set_enabled(
        revoked,
        True,
        principal=_owner(),
        justification="resume",
        now=NOW,
        event_sink=_audit(),
    )

    assert proposed_result.status == DecisionStatus.DENY
    assert revoked_result.status == DecisionStatus.DENY
    with pytest.raises(ValueError, match="timezone-aware"):
        set_enabled(
            approved,
            False,
            principal=_owner(),
            justification="pause",
            now=datetime(2026, 9, 27, 21, 0),
            event_sink=_audit(),
        )


def test_direct_approved_and_revoked_construction_is_rejected():
    with pytest.raises(ValueError, match="lifecycle transition"):
        AutomationRule(
            rule_id="rule-1",
            spec=_spec(),
            status=RuleStatus.APPROVED,
            approved_by="owner-1",
            approved_by_role=RoleTier.OWNER,
            approved_at=NOW,
        )
    with pytest.raises(ValueError, match="lifecycle transition"):
        AutomationRule(
            rule_id="rule-1",
            spec=_spec(),
            status=RuleStatus.REVOKED,
            revoked_by="owner-1",
            revoked_at=NOW,
        )


def test_lifecycle_transition_requires_an_audit_sink():
    rule = propose(_spec(), rule_id="rule-1")
    with pytest.raises(TypeError, match="event_sink"):
        approve(rule, principal=_owner(), justification="trust it", now=NOW)
