"""`AutomationRule`: the propose -> approve -> enable/disable -> revoke
lifecycle for `AutomationSpec`-driven resource-action automations (native
product-consolidation plan §6.1: "reuse it rather than introducing another
scheduler").

What this reuses from the home-automation vertical: `RuleStatus` itself
(PROPOSED/APPROVED/REVOKED) as-is -- the enum carries no device-specific
shape, so there is nothing to generalize. The guard conditions on each
transition (owner-only approval, only a proposed rule can be approved, a
revocation needs an actor and a timestamp, cross-household denial) are
transcribed from `HavenRuntime.approve_rule`/`revoke_rule` so the same
household gets the same rules for resource automations that it already gets
for device ones.

What this deliberately does NOT reuse: `HavenRuntime.store`'s event-sourced
`Transition`/`execute_transition` machinery. That machine is purpose-built
for the device vertical's own append-only audit trail, and the resource-
action vertical already has a lighter-weight pattern of its own --
`ResourceAuthorityEngine.decide()` returns a plain decision value with no
event store behind it, and `ComputerActionService`/`WindowActionService`
record outcomes straight into `ActionLedgerStore`. This lifecycle matches
that existing resource-action pattern (a pure guard function returning an
ordinary result value) rather than bolting resource automations onto an
event-sourcing system built for a different vertical -- reusing the device
rule engine's full state-machine implementation, not just its status
vocabulary, is exactly the "large, safety-critical migration" `schema.py`'s
own docstring says this pass does not attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.domain import DecisionStatus, Principal, RoleTier, RuleStatus
from ..core.time import require_aware_utc
from .schema import AutomationSpec


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class AutomationRule:
    """One `AutomationSpec` moving through propose -> approve -> revoke.

    `enabled` is a separate concern from `status` -- the same distinction
    `SchedulerEngine`'s own disabled-set keeps for device rules -- so
    pausing a resource automation temporarily never disturbs its approval
    record, and re-enabling it later needs no re-approval.
    """

    rule_id: str
    spec: AutomationSpec
    status: RuleStatus = RuleStatus.PROPOSED
    enabled: bool = True
    approved_by: str | None = None
    approved_by_role: RoleTier | None = None
    approved_at: datetime | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _require_text(self.rule_id, name="rule_id"))
        if not isinstance(self.spec, AutomationSpec):
            raise ValueError("spec must be an AutomationSpec")
        if not isinstance(self.status, RuleStatus):
            raise ValueError("status must be a RuleStatus")
        if self.status == RuleStatus.APPROVED:
            if not self.approved_by or self.approved_by_role != RoleTier.OWNER or self.approved_at is None:
                raise ValueError("an approved automation rule requires owner approval and a timestamp")
        if self.approved_by is not None:
            object.__setattr__(self, "approved_by", _require_text(self.approved_by, name="approved_by"))
        if self.approved_at is not None:
            object.__setattr__(self, "approved_at", require_aware_utc(self.approved_at, name="approved_at"))
        if self.status == RuleStatus.REVOKED:
            if not self.revoked_by or self.revoked_at is None:
                raise ValueError("a revoked automation rule requires a revoking actor and timestamp")
        if self.revoked_by is not None:
            object.__setattr__(self, "revoked_by", _require_text(self.revoked_by, name="revoked_by"))
        if self.revoked_at is not None:
            object.__setattr__(self, "revoked_at", require_aware_utc(self.revoked_at, name="revoked_at"))


@dataclass(frozen=True)
class RuleTransitionResult:
    """A guarded lifecycle transition's outcome -- ALLOW with the new rule,
    or DENY with the rule unchanged -- the same "always a result, never a
    raise" shape `ResourceAuthorityEngine.decide()` already uses, so a
    denied approval/revocation is an ordinary, loggable outcome rather than
    an exception every caller must remember to catch."""

    status: DecisionStatus
    reason: str
    rule: AutomationRule


def propose(spec: AutomationSpec, *, rule_id: str) -> AutomationRule:
    """A freshly proposed automation, awaiting owner approval."""

    return AutomationRule(rule_id=rule_id, spec=spec, status=RuleStatus.PROPOSED)


def set_enabled(rule: AutomationRule, enabled: bool) -> AutomationRule:
    """Pause/resume without touching approval or revocation state -- the
    `AutomationRule` equivalent of `SchedulerEngine.set_enabled`, except the
    flag lives on the rule value itself rather than a scheduler-side set,
    since (unlike a device `Rule`) `AutomationRule` already has room for it."""

    return AutomationRule(
        rule_id=rule.rule_id,
        spec=rule.spec,
        status=rule.status,
        enabled=enabled,
        approved_by=rule.approved_by,
        approved_by_role=rule.approved_by_role,
        approved_at=rule.approved_at,
        revoked_by=rule.revoked_by,
        revoked_at=rule.revoked_at,
    )


def approve(
    rule: AutomationRule,
    *,
    principal: Principal,
    justification: str,
    now: datetime,
) -> RuleTransitionResult:
    now = require_aware_utc(now, name="approval time")
    if principal.household_id != rule.spec.household_id:
        return RuleTransitionResult(
            DecisionStatus.DENY, "the approving principal must belong to the automation's household", rule
        )
    if principal.role_tier < RoleTier.OWNER:
        return RuleTransitionResult(
            DecisionStatus.DENY, "only a household owner can approve an autonomous resource action", rule
        )
    if not isinstance(justification, str) or not justification.strip():
        return RuleTransitionResult(DecisionStatus.DENY, "approval requires a non-empty justification", rule)
    if rule.status != RuleStatus.PROPOSED:
        return RuleTransitionResult(DecisionStatus.DENY, "only a proposed automation can be approved", rule)

    approved = AutomationRule(
        rule_id=rule.rule_id,
        spec=rule.spec,
        status=RuleStatus.APPROVED,
        enabled=rule.enabled,
        approved_by=principal.actor_id,
        approved_by_role=principal.role_tier,
        approved_at=now,
    )
    return RuleTransitionResult(DecisionStatus.ALLOW, "owner approval is valid for this automation", approved)


def revoke(
    rule: AutomationRule,
    *,
    principal: Principal,
    justification: str,
    now: datetime,
) -> RuleTransitionResult:
    now = require_aware_utc(now, name="revocation time")
    if principal.household_id != rule.spec.household_id:
        return RuleTransitionResult(
            DecisionStatus.DENY, "the revoking principal must belong to the automation's household", rule
        )
    if principal.role_tier < RoleTier.OWNER:
        return RuleTransitionResult(
            DecisionStatus.DENY, "only a household owner can revoke an autonomous resource action", rule
        )
    if not isinstance(justification, str) or not justification.strip():
        return RuleTransitionResult(DecisionStatus.DENY, "revocation requires a non-empty justification", rule)
    if rule.status == RuleStatus.REVOKED:
        return RuleTransitionResult(DecisionStatus.DENY, "this automation is already revoked", rule)

    revoked = AutomationRule(
        rule_id=rule.rule_id,
        spec=rule.spec,
        status=RuleStatus.REVOKED,
        enabled=rule.enabled,
        approved_by=rule.approved_by,
        approved_by_role=rule.approved_by_role,
        approved_at=rule.approved_at,
        revoked_by=principal.actor_id,
        revoked_at=now,
    )
    return RuleTransitionResult(DecisionStatus.ALLOW, "revoked by household owner", revoked)


__all__ = ["AutomationRule", "RuleTransitionResult", "approve", "propose", "revoke", "set_enabled"]
