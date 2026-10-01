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
record outcomes straight into `ActionLedgerStore`. This lifecycle keeps that
resource-action boundary, but every approval/revocation/enablement transition
now requires an explicit audit-event sink. It therefore does not silently
return a changed rule with no attributable record, while still avoiding a
second device-specific event-sourcing implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from uuid import uuid4

from ..core.domain import DecisionStatus, Principal, RoleTier, RuleStatus
from ..core.time import require_aware_utc
from .schema import AutomationSpec


_LIFECYCLE_TOKEN = object()
_LIFECYCLE_TRANSITIONS = frozenset({"approve", "revoke", "set_enabled"})
_MISSING_JUSTIFICATION = "[missing justification]"


def _new_event_id() -> str:
    return f"automation-event-{uuid4().hex}"


def _new_correlation_id() -> str:
    return f"automation-correlation-{uuid4().hex}"


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, init=False)
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
    _lifecycle_token: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        rule_id: str,
        spec: AutomationSpec,
        status: RuleStatus = RuleStatus.PROPOSED,
        enabled: bool = True,
        approved_by: str | None = None,
        approved_by_role: RoleTier | None = None,
        approved_at: datetime | None = None,
        revoked_by: str | None = None,
        revoked_at: datetime | None = None,
        *,
        _lifecycle_token: object | None = None,
    ) -> None:
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "approved_by", approved_by)
        object.__setattr__(self, "approved_by_role", approved_by_role)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "revoked_by", revoked_by)
        object.__setattr__(self, "revoked_at", revoked_at)
        object.__setattr__(self, "_lifecycle_token", _lifecycle_token)
        self._validate(_lifecycle_token)

    def _validate(self, lifecycle_token: object | None) -> None:
        object.__setattr__(self, "rule_id", _require_text(self.rule_id, name="rule_id"))
        if not isinstance(self.spec, AutomationSpec):
            raise ValueError("spec must be an AutomationSpec")
        if not isinstance(self.status, RuleStatus):
            raise ValueError("status must be a RuleStatus")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a bool")
        if self.status is RuleStatus.PROPOSED:
            if any(
                value is not None
                for value in (
                    self.approved_by,
                    self.approved_by_role,
                    self.approved_at,
                    self.revoked_by,
                    self.revoked_at,
                )
            ):
                raise ValueError("a proposed automation rule cannot carry approval or revocation metadata")
        elif lifecycle_token is not _LIFECYCLE_TOKEN:
            raise ValueError("approved or revoked automation rules must be created by a lifecycle transition")
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
class AutomationLifecycleEvent:
    """Auditable result of one resource-automation lifecycle transition.

    The lifecycle is intentionally not backed by the device-specific
    ``HavenStore``. Requiring the caller to accept this event through an
    explicit sink keeps that boundary honest: a caller cannot receive a new
    approved/enabled/revoked value without also handling the corresponding
    audit record.
    """

    event_id: str
    transition: str
    household_id: str
    rule_id: str
    actor_id: str
    status: DecisionStatus
    enabled: bool
    justification: str
    occurred_at: datetime
    correlation_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "transition",
            "household_id",
            "rule_id",
            "actor_id",
            "correlation_id",
        ):
            _require_text(getattr(self, field_name), name=field_name)
        if self.transition not in _LIFECYCLE_TRANSITIONS:
            raise ValueError(f"transition must be one of {sorted(_LIFECYCLE_TRANSITIONS)}")
        if not isinstance(self.justification, str) or not self.justification.strip():
            raise ValueError("justification must be a non-empty string")
        object.__setattr__(self, "justification", self.justification.strip())
        if not isinstance(self.status, DecisionStatus):
            raise ValueError("status must be a DecisionStatus")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a bool")
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at, name="event time"))


AutomationEventSink = Callable[[AutomationLifecycleEvent], None]


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
    event: AutomationLifecycleEvent


def _emit_result(
    rule: AutomationRule,
    *,
    transition: str,
    status: DecisionStatus,
    reason: str,
    principal: Principal,
    justification: str,
    now: datetime,
    enabled: bool,
    correlation_id: str | None,
    event_sink: AutomationEventSink,
) -> RuleTransitionResult:
    if not callable(event_sink):
        raise TypeError("event_sink must be callable")
    audit_justification = (
        justification.strip()
        if isinstance(justification, str) and justification.strip()
        else _MISSING_JUSTIFICATION
    )
    audit_event = AutomationLifecycleEvent(
        event_id=_new_event_id(),
        transition=transition,
        household_id=rule.spec.household_id,
        rule_id=rule.rule_id,
        actor_id=principal.actor_id,
        status=status,
        enabled=enabled,
        justification=audit_justification,
        occurred_at=now,
        correlation_id=(
            correlation_id.strip()
            if isinstance(correlation_id, str) and correlation_id.strip()
            else _new_correlation_id()
        ),
    )
    event_sink(audit_event)
    return RuleTransitionResult(status, reason, rule, audit_event)


def _lifecycle_rule(**values: object) -> AutomationRule:
    return AutomationRule(_lifecycle_token=_LIFECYCLE_TOKEN, **values)


def propose(spec: AutomationSpec, *, rule_id: str) -> AutomationRule:
    """A freshly proposed automation, awaiting owner approval."""

    return AutomationRule(rule_id=rule_id, spec=spec, status=RuleStatus.PROPOSED)


def rehydrate(
    *,
    rule_id: str,
    spec: AutomationSpec,
    status: RuleStatus,
    enabled: bool = True,
    approved_by: str | None = None,
    approved_by_role: RoleTier | None = None,
    approved_at: datetime | None = None,
    revoked_by: str | None = None,
    revoked_at: datetime | None = None,
) -> AutomationRule:
    """Restore a previously persisted rule at a process boundary.

    Persisted approved/revoked values must not be constructible by ordinary
    callers, but a restart needs a narrow, explicit rehydration seam. Keeping
    the lifecycle token here preserves the structural guard while making the
    persistence boundary auditable and testable.
    """

    return _lifecycle_rule(
        rule_id=rule_id,
        spec=spec,
        status=status,
        enabled=enabled,
        approved_by=approved_by,
        approved_by_role=approved_by_role,
        approved_at=approved_at,
        revoked_by=revoked_by,
        revoked_at=revoked_at,
    )


def approve(
    rule: AutomationRule,
    *,
    principal: Principal,
    justification: str,
    now: datetime,
    event_sink: AutomationEventSink,
    correlation_id: str | None = None,
) -> RuleTransitionResult:
    now = require_aware_utc(now, name="approval time")
    if principal.household_id != rule.spec.household_id:
        return _emit_result(
            rule,
            transition="approve",
            status=DecisionStatus.DENY,
            reason="the approving principal must belong to the automation's household",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if principal.role_tier < RoleTier.OWNER:
        return _emit_result(
            rule,
            transition="approve",
            status=DecisionStatus.DENY,
            reason="only a household owner can approve an autonomous resource action",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if not isinstance(justification, str) or not justification.strip():
        return _emit_result(
            rule,
            transition="approve",
            status=DecisionStatus.DENY,
            reason="approval requires a non-empty justification",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if rule.status != RuleStatus.PROPOSED:
        return _emit_result(
            rule,
            transition="approve",
            status=DecisionStatus.DENY,
            reason="only a proposed automation can be approved",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )

    approved = _lifecycle_rule(
        rule_id=rule.rule_id,
        spec=rule.spec,
        status=RuleStatus.APPROVED,
        enabled=rule.enabled,
        approved_by=principal.actor_id,
        approved_by_role=principal.role_tier,
        approved_at=now,
    )
    return _emit_result(
        approved,
        transition="approve",
        status=DecisionStatus.ALLOW,
        reason="owner approval is valid for this automation",
        principal=principal,
        justification=justification,
        now=now,
        enabled=approved.enabled,
        correlation_id=correlation_id,
        event_sink=event_sink,
    )


def revoke(
    rule: AutomationRule,
    *,
    principal: Principal,
    justification: str,
    now: datetime,
    event_sink: AutomationEventSink,
    correlation_id: str | None = None,
) -> RuleTransitionResult:
    now = require_aware_utc(now, name="revocation time")
    if principal.household_id != rule.spec.household_id:
        return _emit_result(
            rule,
            transition="revoke",
            status=DecisionStatus.DENY,
            reason="the revoking principal must belong to the automation's household",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if principal.role_tier < RoleTier.OWNER:
        return _emit_result(
            rule,
            transition="revoke",
            status=DecisionStatus.DENY,
            reason="only a household owner can revoke an autonomous resource action",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if not isinstance(justification, str) or not justification.strip():
        return _emit_result(
            rule,
            transition="revoke",
            status=DecisionStatus.DENY,
            reason="revocation requires a non-empty justification",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if rule.status == RuleStatus.REVOKED:
        return _emit_result(
            rule,
            transition="revoke",
            status=DecisionStatus.DENY,
            reason="this automation is already revoked",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )

    revoked = _lifecycle_rule(
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
    return _emit_result(
        revoked,
        transition="revoke",
        status=DecisionStatus.ALLOW,
        reason="revoked by household owner",
        principal=principal,
        justification=justification,
        now=now,
        enabled=revoked.enabled,
        correlation_id=correlation_id,
        event_sink=event_sink,
    )


def set_enabled(
    rule: AutomationRule,
    enabled: bool,
    *,
    principal: Principal,
    justification: str,
    now: datetime,
    event_sink: AutomationEventSink,
    correlation_id: str | None = None,
) -> RuleTransitionResult:
    """Pause or resume an approved automation through a governed transition."""

    now = require_aware_utc(now, name="enablement time")
    if principal.household_id != rule.spec.household_id:
        return _emit_result(
            rule,
            transition="set_enabled",
            status=DecisionStatus.DENY,
            reason="the changing principal must belong to the automation's household",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if principal.role_tier < RoleTier.OWNER:
        return _emit_result(
            rule,
            transition="set_enabled",
            status=DecisionStatus.DENY,
            reason="only a household owner can pause or resume an autonomous resource action",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if not isinstance(justification, str) or not justification.strip():
        return _emit_result(
            rule,
            transition="set_enabled",
            status=DecisionStatus.DENY,
            reason="enablement changes require a non-empty justification",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if not isinstance(enabled, bool):
        return _emit_result(
            rule,
            transition="set_enabled",
            status=DecisionStatus.DENY,
            reason="enabled must be a bool",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )
    if rule.status != RuleStatus.APPROVED:
        return _emit_result(
            rule,
            transition="set_enabled",
            status=DecisionStatus.DENY,
            reason="only an approved automation can be paused or resumed",
            principal=principal,
            justification=justification,
            now=now,
            enabled=rule.enabled,
            correlation_id=correlation_id,
            event_sink=event_sink,
        )

    updated = _lifecycle_rule(
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
    return _emit_result(
        updated,
        transition="set_enabled",
        status=DecisionStatus.ALLOW,
        reason="owner enablement change is valid for this automation",
        principal=principal,
        justification=justification,
        now=now,
        enabled=updated.enabled,
        correlation_id=correlation_id,
        event_sink=event_sink,
    )


__all__ = [
    "AutomationEventSink",
    "AutomationLifecycleEvent",
    "AutomationRule",
    "RuleTransitionResult",
    "approve",
    "propose",
    "rehydrate",
    "revoke",
    "set_enabled",
]
