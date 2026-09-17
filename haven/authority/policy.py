"""Authority policy for the first HAVEN vertical slice."""

from __future__ import annotations

from datetime import datetime, timedelta

from haven.core.domain import (
    ActionKind,
    ActionRequest,
    AuthorityDecision,
    ChangeOrigin,
    DecisionCode,
    DecisionStatus,
    Principal,
    RiskTier,
    RoleTier,
    Rule,
    RuleStatus,
    WorldSnapshot,
)
from haven.devices import ControlClass, DeviceRegistry, UnknownCapability, UnknownDevice


# "A recent explicit human action beats automation." 90 minutes is a starting
# default, not a tuned value -- pass a different `human_override_window` to
# AuthorityEngine to change it per household or per deployment.
HUMAN_OVERRIDE_WINDOW = timedelta(minutes=90)

# Fail closed by default: evidence below full confidence (1.0) -- e.g. a
# vision or IR observation, which is inherently probabilistic rather than a
# device's own reported state -- cannot authorize an action unless a
# household explicitly lowers this via `minimum_confidence`. This keeps
# probabilistic perception an opt-in capability rather than a silent
# default, matching Haven Core working with zero cameras.
DEFAULT_MINIMUM_CONFIDENCE = 1.0


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
        ActionKind.CLOSE_GARAGE,
        ActionKind.UNLOCK_DOOR,
        ActionKind.PURCHASE,
        ActionKind.CHANGE_ALARM,
    }
)
FORBIDDEN = frozenset({ActionKind.UNSCOPED_EXECUTION})

# A device's declared ControlClass maps onto the same RiskTier vocabulary
# used for the closed ActionKind set below, so both paths are judged by the
# same AuthorityEngine branches. READ never reaches AuthorityEngine.decide()
# (only a write action produces an ActionRequest), but it is mapped for
# completeness rather than left to raise.
CONTROL_CLASS_RISK = {
    ControlClass.READ: RiskTier.SAFE_AUTOMATIC,
    ControlClass.LOW_RISK: RiskTier.SAFE_AUTOMATIC,
    ControlClass.MEDIUM: RiskTier.CONDITIONAL,
    ControlClass.GUARDED: RiskTier.CONFIRMATION_REQUIRED,
}


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


def risk_for_request(request: ActionRequest, *, device_registry: DeviceRegistry | None) -> tuple[RiskTier, DecisionCode | None]:
    """Resolve risk from a device's declared capability when one is named.

    A request that names no capability is classified by the closed
    `ActionKind` set, exactly as before. A request that does name a
    capability is classified from that device's manifest instead -- and if
    the device or capability cannot be resolved, the result is FORBIDDEN
    with UNKNOWN_CAPABILITY rather than silently falling back to
    `ActionKind`, since a caller that names a capability is asserting the
    manifest is authoritative for this decision.
    """

    if request.capability is None:
        return risk_for(request.action_kind), None
    if device_registry is None:
        return RiskTier.FORBIDDEN, DecisionCode.UNKNOWN_CAPABILITY
    try:
        manifest = device_registry.get(request.target_device_id)
        capability = manifest.capability(request.capability)
    except (UnknownDevice, UnknownCapability):
        return RiskTier.FORBIDDEN, DecisionCode.UNKNOWN_CAPABILITY
    if not capability.writable:
        return RiskTier.FORBIDDEN, DecisionCode.UNKNOWN_CAPABILITY
    return CONTROL_CLASS_RISK[capability.control_class], None


class AuthorityEngine:
    """Evaluate a request against scope, evidence, lifecycle, and risk."""

    def __init__(
        self,
        *,
        device_registry: DeviceRegistry | None = None,
        human_override_window: timedelta = HUMAN_OVERRIDE_WINDOW,
        minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    ) -> None:
        self.device_registry = device_registry
        self.human_override_window = human_override_window
        self.minimum_confidence = minimum_confidence

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
        if rule.draft.expires_at is not None and now > rule.draft.expires_at:
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.RULE_EXPIRED,
                "this rule's expires_at has passed; it can no longer authorize an action",
            )
        if rule.draft.target_selector is not None:
            resolved = self.device_registry.resolve(rule.draft.target_selector) if self.device_registry else ()
            device_matches = request.target_device_id in resolved
        else:
            device_matches = request.target_device_id == rule.draft.target_device_id

        if (
            request.action_kind != rule.draft.action_kind
            or not device_matches
            or request.parameters != rule.draft.parameters
            or request.capability != rule.draft.capability
        ):
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.ACTION_MISMATCH,
                "the requested action must match the approved rule exactly",
            )

        risk, risk_code = risk_for_request(request, device_registry=self.device_registry)
        if risk_code is not None:
            return AuthorityDecision(
                DecisionStatus.DENY,
                risk_code,
                "the request names a capability that its device manifest does not authorize",
            )
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

        trigger_active, trigger_code = world.evaluate_trigger(
            rule.draft,
            at=now,
            target_device_id=request.target_device_id,
            minimum_confidence=self.minimum_confidence,
        )
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
            if trigger_code == DecisionCode.LOW_CONFIDENCE_EVIDENCE:
                return AuthorityDecision(
                    DecisionStatus.UNAVAILABLE,
                    trigger_code,
                    "required household evidence is below this engine's minimum_confidence",
                )
            return AuthorityDecision(
                DecisionStatus.DENY,
                trigger_code,
                "the approved rule trigger is not active",
            )

        # A human who just touched this device directly outranks automation,
        # regardless of risk tier: this is checked before CONFIRMATION_REQUIRED
        # so a fresh manual change suspends a rule outright rather than merely
        # asking for confirmation to override it.
        device_state = world.device_for(request.target_device_id)
        if device_state is not None and device_state.changed_by == ChangeOrigin.HUMAN:
            since_change = now - device_state.observed_at
            if since_change <= self.human_override_window:
                return AuthorityDecision(
                    DecisionStatus.DENY,
                    DecisionCode.HUMAN_OVERRIDE_ACTIVE,
                    "a household member changed this device directly within the "
                    "override window; automation is suspended until it expires",
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

    def decide_direct(
        self,
        request: ActionRequest,
        *,
        principal: Principal,
        world: WorldSnapshot,
        now: datetime,
        confirmation_consumed: bool = False,
    ) -> AuthorityDecision:
        """Decide a HUMAN-INITIATED one-shot action.

        A member directly commanding a device is itself the authorization;
        automation needs a rule, a command does not. So every `decide()`
        check that exists to police a rule is intentionally absent here:

        - rule lifecycle gates (`RULE_NOT_APPROVED`,
          `RULE_APPROVAL_INSUFFICIENT`, unresolved interpretation items,
          `RULE_EXPIRED`) -- there is no rule;
        - `ACTION_MISMATCH` -- the human's command is compared to nothing;
        - trigger evaluation and all of its evidence gates
          (`TRIGGER_NOT_ACTIVE`, `EVIDENCE_MISSING`, `EVIDENCE_UNAVAILABLE`,
          `STALE_EVIDENCE`, `LOW_CONFIDENCE_EVIDENCE`) --
          the human asserted the command, and execution observes the
          consequence rather than a rule inferring it from evidence;
        - the `HUMAN_OVERRIDE_ACTIVE` suspension -- the requester IS the
          human, so a recent manual change must not block the human's own
          new command.

        What remains, identical to `decide()`: household scope, actor
        match, the MEMBER role floor, a non-empty justification, risk
        classification via `risk_for_request`, the brightness parameter
        gate, and -- for CONFIRMATION_REQUIRED risk -- the same single-use,
        bound, time-limited confirmation token logic.
        """

        if (
            request.household_id != principal.household_id
            or request.household_id != world.household_id
        ):
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.CROSS_HOUSEHOLD,
                "the request, principal, and world snapshot must share one household",
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
                "a household member or higher role is required to command an action directly",
                required_role=RoleTier.MEMBER,
            )
        if not request.justification.strip():
            return AuthorityDecision(
                DecisionStatus.DENY,
                DecisionCode.MISSING_JUSTIFICATION,
                "an action request must carry a non-empty justification",
            )

        risk, risk_code = risk_for_request(request, device_registry=self.device_registry)
        if risk_code is not None:
            return AuthorityDecision(
                DecisionStatus.DENY,
                risk_code,
                "the request names a capability that its device manifest does not authorize",
            )
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
            "household scope, role, and the member's own command satisfy policy",
        )


__all__ = [
    "AuthorityEngine",
    "CONFIRMATION_REQUIRED",
    "CONTROL_CLASS_RISK",
    "DEFAULT_MINIMUM_CONFIDENCE",
    "FORBIDDEN",
    "HUMAN_OVERRIDE_WINDOW",
    "SAFE_AUTOMATIC",
    "risk_for",
    "risk_for_request",
]
