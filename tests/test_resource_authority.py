"""`ResourceAuthorityEngine`: household scope, actor match, role floor,
justification, risk-tiered confirmation -- the same discipline
`AuthorityEngine.decide_direct` applies to a device command, generalized to
an open, provider-owned action vocabulary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from haven.actions import ResourceActionRequest, ResourceAuthorityEngine
from haven.core.domain import ConfirmationToken, DecisionStatus, Principal, RiskTier, RoleTier

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

RISK = {
    "filesystem.create_folder": RiskTier.SAFE_AUTOMATIC,
    "filesystem.copy": RiskTier.SAFE_AUTOMATIC,
    "filesystem.move": RiskTier.CONFIRMATION_REQUIRED,
}


def _request(
    *,
    action="filesystem.create_folder",
    household_id="haven-1",
    requested_by="person:gerron",
    resource_id=None,
    justification="user requested via System > Files",
    confirmation_token=None,
) -> ResourceActionRequest:
    return ResourceActionRequest(
        request_id="req-1",
        household_id=household_id,
        requested_by=requested_by,
        provider_id="local_filesystem",
        action=action,
        resource_id=resource_id,
        parameters=(("path", "C:/Docs/New Folder"),),
        justification=justification,
        requested_at=NOW,
        confirmation_token=confirmation_token,
    )


def _principal(*, actor_id="person:gerron", household_id="haven-1", role_tier=RoleTier.MEMBER) -> Principal:
    return Principal(actor_id=actor_id, household_id=household_id, role_tier=role_tier)


def _engine() -> ResourceAuthorityEngine:
    return ResourceAuthorityEngine(action_risk=RISK)


def test_a_safe_automatic_action_is_allowed_without_confirmation():
    decision = _engine().decide(_request(), principal=_principal(), now=NOW)
    assert decision.status == DecisionStatus.ALLOW


def test_cross_household_request_is_denied():
    decision = _engine().decide(
        _request(household_id="haven-1"), principal=_principal(household_id="haven-2"), now=NOW
    )
    assert decision.status == DecisionStatus.DENY


def test_actor_mismatch_is_denied():
    decision = _engine().decide(
        _request(requested_by="person:gerron"), principal=_principal(actor_id="person:someone-else"), now=NOW
    )
    assert decision.status == DecisionStatus.DENY


def test_below_minimum_role_is_denied():
    decision = _engine().decide(_request(), principal=_principal(role_tier=RoleTier.GUEST), now=NOW)
    assert decision.status == DecisionStatus.DENY
    assert decision.required_role == RoleTier.MEMBER


def test_missing_justification_is_denied():
    decision = _engine().decide(_request(justification="   "), principal=_principal(), now=NOW)
    assert decision.status == DecisionStatus.DENY


def test_an_unknown_action_is_denied_not_silently_allowed():
    decision = _engine().decide(_request(action="filesystem.delete_everything"), principal=_principal(), now=NOW)
    assert decision.status == DecisionStatus.DENY


def test_a_forbidden_classified_action_is_denied():
    engine = ResourceAuthorityEngine(action_risk={"filesystem.wipe": RiskTier.FORBIDDEN})
    decision = engine.decide(_request(action="filesystem.wipe"), principal=_principal(), now=NOW)
    assert decision.status == DecisionStatus.DENY


def test_confirmation_required_action_without_a_token_asks_for_one():
    decision = _engine().decide(_request(action="filesystem.move"), principal=_principal(), now=NOW)
    assert decision.status == DecisionStatus.CONFIRMATION_REQUIRED


def test_confirmation_required_action_with_a_valid_token_is_allowed():
    token = ConfirmationToken(
        token_id="confirm-1",
        household_id="haven-1",
        rule_id="req-1",
        request_id="req-1",
        confirmed_by="person:gerron",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    decision = _engine().decide(
        _request(action="filesystem.move", confirmation_token=token), principal=_principal(), now=NOW
    )
    assert decision.status == DecisionStatus.ALLOW


def test_confirmation_required_action_with_an_expired_token_asks_again():
    token = ConfirmationToken(
        token_id="confirm-1",
        household_id="haven-1",
        rule_id="req-1",
        request_id="req-1",
        confirmed_by="person:gerron",
        issued_at=NOW - timedelta(minutes=10),
        expires_at=NOW - timedelta(minutes=5),
    )
    decision = _engine().decide(
        _request(action="filesystem.move", confirmation_token=token), principal=_principal(), now=NOW
    )
    assert decision.status == DecisionStatus.CONFIRMATION_REQUIRED


def test_a_reused_confirmation_is_denied():
    token = ConfirmationToken(
        token_id="confirm-1",
        household_id="haven-1",
        rule_id="req-1",
        request_id="req-1",
        confirmed_by="person:gerron",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    decision = _engine().decide(
        _request(action="filesystem.move", confirmation_token=token),
        principal=_principal(),
        now=NOW,
        confirmation_consumed=True,
    )
    assert decision.status == DecisionStatus.DENY


def test_a_token_bound_to_a_different_request_does_not_satisfy_this_one():
    token = ConfirmationToken(
        token_id="confirm-1",
        household_id="haven-1",
        rule_id="some-other-request",
        request_id="some-other-request",
        confirmed_by="person:gerron",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    decision = _engine().decide(
        _request(action="filesystem.move", confirmation_token=token), principal=_principal(), now=NOW
    )
    assert decision.status == DecisionStatus.CONFIRMATION_REQUIRED
