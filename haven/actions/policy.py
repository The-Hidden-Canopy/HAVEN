"""`ResourceAuthorityEngine`: the same discipline
`haven.authority.policy.AuthorityEngine.decide_direct` applies to a device
command, generalized to any provider's resource-scoped write action.

There is only one `decide()` here, not a `decide()`/`decide_direct()` split
the way the device pipeline has: every resource-provider action today is a
human-initiated, one-shot command -- there is no rule/automation concept for
"move this file every night at 9pm" yet, so there is nothing to trigger from
evidence and nothing for `ACTION_MISMATCH` to compare against. When a
resource-action automation concept is real, it gets its own entry point
rather than this one growing rule-shaped branches it does not need yet.

Risk is looked up from a table the caller supplies (`action_risk`), keyed by
the request's own open `action` string -- unlike `haven.authority.policy`'s
`SAFE_AUTOMATIC`/`CONFIRMATION_REQUIRED`/`FORBIDDEN` frozensets, which are
closed over the device vertical's fixed `ActionKind` enum, a resource
provider's action vocabulary is open (see `models.py`), so this engine asks
the caller for that classification rather than hardcoding one. An action
missing from the table is treated exactly like `FORBIDDEN`: fail closed,
the same "new kinds must be classified explicitly" discipline
`haven.authority.policy.risk_for` already applies to devices.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from haven.core.domain import DecisionStatus, Principal, RiskTier, RoleTier

from .models import ResourceActionDecision, ResourceActionRequest

DEFAULT_CONFIRMATION_WINDOW = timedelta(minutes=5)


class ResourceAuthorityEngine:
    def __init__(
        self,
        *,
        action_risk: Mapping[str, RiskTier],
        minimum_role: RoleTier = RoleTier.MEMBER,
        confirmation_window: timedelta = DEFAULT_CONFIRMATION_WINDOW,
    ) -> None:
        self._action_risk = dict(action_risk)
        self._minimum_role = minimum_role
        self.confirmation_window = confirmation_window

    def decide(
        self,
        request: ResourceActionRequest,
        *,
        principal: Principal,
        now: datetime,
        confirmation_consumed: bool = False,
    ) -> ResourceActionDecision:
        if request.household_id != principal.household_id:
            return ResourceActionDecision(
                DecisionStatus.DENY, "the request and principal must share one household"
            )
        if request.requested_by != principal.actor_id:
            return ResourceActionDecision(
                DecisionStatus.DENY, "the action requester must match the principal being evaluated"
            )
        if principal.role_tier < self._minimum_role:
            return ResourceActionDecision(
                DecisionStatus.DENY,
                f"a household {self._minimum_role.name.lower()} or higher role is required",
                required_role=self._minimum_role,
            )
        if not request.justification.strip():
            return ResourceActionDecision(
                DecisionStatus.DENY, "an action request must carry a non-empty justification"
            )

        risk = self._action_risk.get(request.action)
        if risk is None or risk is RiskTier.FORBIDDEN:
            return ResourceActionDecision(DecisionStatus.DENY, f"{request.action!r} is not an authorized action")

        if risk is RiskTier.CONFIRMATION_REQUIRED:
            token = request.confirmation_token
            if confirmation_consumed:
                return ResourceActionDecision(DecisionStatus.DENY, "the confirmation token was already consumed")
            if token is None or not token.is_valid_for(
                household_id=request.household_id,
                rule_id=request.request_id,
                request_id=request.request_id,
                confirmed_by=principal.actor_id,
                at=now,
            ):
                return ResourceActionDecision(
                    DecisionStatus.CONFIRMATION_REQUIRED,
                    "this action requires a valid, unexpired confirmation token bound to this request",
                )

        return ResourceActionDecision(DecisionStatus.ALLOW, "household scope, role, and risk policy are satisfied")


__all__ = ["DEFAULT_CONFIRMATION_WINDOW", "ResourceAuthorityEngine"]
