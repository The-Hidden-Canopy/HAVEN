"""Authority policy for the first HAVEN vertical slice."""

from __future__ import annotations

from datetime import datetime

from haven.core.domain import (
    ActionKind,
    ActionRequest,
    AuthorityDecision,
    DecisionCode,
    DecisionStatus,
    Principal,
    RiskTier,
    RoleTier,
    Rule,
    RuleStatus,
    WorldSnapshot,
)


SAFE_AUTOMATIC = frozenset(
    {
        ActionKind.TURN_LIGHT_OFF,
        ActionKind.SET_LIGHT_BRIGHTNESS,
        ActionKind.ACTIVATE_SCENE,
        ActionKind.SET_THERMOSTAT,
    }
)
CONFIRMATION_REQUIRED = frozenset(
    {
        ActionKind.OPEN_GARAGE,
        ActionKind.UNLOCK_DOOR,
        ActionKind.PURCHASE,
        ActionKind.CHANGE_ALARM,
    }
)
FORBIDDEN = frozenset({ActionKind.UNSCOPED_EXECUTION})


def risk_for(action_kind: ActionKind) -> RiskTier:
    if action_kind in SAFE_AUTOMATIC:
        return RiskTier.SAFE_AUTOMATIC
    if action_kind in CONFIRMATION_REQUIRED:
        return RiskTier.CONFIRMATION_REQUIRED
    if action_kind in FORBIDDEN:
        return RiskTier.FORBIDDEN
    # New action kinds must be classified explicitly before they can cross the
    # integration boundary. Unknown is not a safe default.
    return RiskTier.FORBIDDEN


class AuthorityEngine:
    """Evaluate a request against scope, evidence, lifecycle, and risk."""

    def decide(
        self,
        request: ActionRequest,
        *,
        principal: Principal,
        rule: Rule,
        world: WorldSnapshot,
        now: datetime,
        confirmation_consumed: bool = False,
    ) -> AuthorityDecision:
        # Scope is checked first. No foreign world material should be joined to
        # a request before this branch has passed.
        if (
            request.household_id != principal.household_id
            or request.household_id != rule.draft.household_id
            or request.household_id != world.household_id
        ):
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.CROSS_HOUSEHOLD,
                "the request, rule, principal, and world snapshot must share one household",
            )
        if request.requested_by != principal.actor_id:
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.ACTOR_MISMATCH,
                "the action requester must match the principal being evaluated",
            )
        if principal.role_tier < RoleTier.MEMBER:
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.WRONG_ROLE_TIER,
                "a household member or higher role is required to trigger automation",
                required_role=RoleTier.MEMBER,
            )
        if not request.justification.strip():
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.MISSING_JUSTIFICATION,
                "an action request must carry a non-empty justification",
            )
        if rule.status != RuleStatus.APPROVED:
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.RULE_NOT_APPROVED,
                "a proposed or revoked rule cannot authorize an action",
                required_role=RoleTier.OWNER,
            )
        if rule.approved_by_role != RoleTier.OWNER:
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.RULE_APPROVAL_INSUFFICIENT,
                "autonomous execution requires recorded owner approval",
                required_role=RoleTier.OWNER,
            )
        if rule.draft.unresolved:
            return AuthorityDecision(
                DecisionStatus.NEEDS_CLARIFICATION,
                DecisionCode.NEEDS_CLARIFICATION,
                "the approved rule still contains unresolved interpretation items",
            )
        if (
            request.action_kind != rule.draft.action_kind
            or request.target_device_id != rule.draft.target_device_id
            or request.parameters != rule.draft.parameters
        ):
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.ACTION_MISMATCH,
                "the requested action must match the approved rule exactly",
            )

        risk = risk_for(request.action_kind)
        if risk == RiskTier.FORBIDDEN:
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.FORBIDDEN_ACTION,
                "this action is outside explicit household authority",
            )
        if request.action_kind == ActionKind.SET_LIGHT_BRIGHTNESS:
            parameter_names = {key for key, _ in request.parameters}
            if "brightness_pct" not in parameter_names:
                return AuthorityDecision(
                    DecisionStatus.NEEDS_CLARIFICATION,
                    DecisionCode.NEEDS_CLARIFICATION,
                    "a brightness action requires an explicit brightness_pct parameter",
                )

        trigger_active, trigger_code = world.evaluate_trigger(rule.draft, at=now)
        if not trigger_active:
            if trigger_code == DecisionCode.EVIDENCE_UNAVAILABLE:
                return AuthorityDecision(
                    DecisionStatus.UNAVAILABLE,
                    trigger_code,
                    "required household evidence is unavailable",
                )
            if trigger_code == DecisionCode.STALE_EVIDENCE:
                return AuthorityDecision(
                    DecisionStatus.UNAVAILABLE,
                    trigger_code,
                    "required household evidence is stale or outside the snapshot validity window",
                )
            if trigger_code == DecisionCode.EVIDENCE_MISSING:
                return AuthorityDecision(
                    DecisionStatus.UNAVAILABLE,
                    trigger_code,
                    "required household evidence is missing",
                )
            return AuthorityDecision(
                DecisionStatus.DENY,
                trigger_code,
                "the approved rule trigger is not active",
            )

        if risk == RiskTier.CONFIRMATION_REQUIRED:
            token = request.confirmation_token
            if confirmation_consumed:
                return AuthorityDecision(
                    DecisionStatus.DENY,
                    DecisionCode.CONFIRMATION_REUSED,
                    "the confirmation token was already consumed",
                )
            if token is None or not token.is_valid_for(
                household_id=request.household_id,
                rule_id=request.rule_id,
                request_id=request.request_id,
                confirmed_by=principal.actor_id,
                at=now,
            ):
                return AuthorityDecision(
                    DecisionStatus.CONFIRMATION_REQUIRED,
                    DecisionCode.CONFIRMATION_REQUIRED,
                    "this action requires a valid, unexpired confirmation token bound to this request",
                )
        return AuthorityDecision(
            DecisionStatus.ALLOW,
            DecisionCode.ALLOWED,
            "approved rule, household scope, role, and current evidence satisfy policy",
        )


__all__ = [
    "AuthorityEngine",
    "CONFIRMATION_REQUIRED",
    "FORBIDDEN",
    "SAFE_AUTOMATIC",
    "risk_for",
]
