"""Orchestration for the first HAVEN vertical slice."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from haven.audit.receipts import ActionReceipt
from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ActionOrigin,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    AuthorityDecision,
    ConfirmationToken,
    DecisionCode,
    DecisionStatus,
    DeviceCommand,
    DeviceSelector,
    DomainEvent,
    EventType,
    EvidenceRef,
    Principal,
    Rule,
    RuleDraft,
    RuleStatus,
    RoleTier,
    Transition,
    TransitionKind,
    WorldSnapshot,
)
from haven.core.store import HavenStore, RuleApproval, RuleClarification, RuleDecision
from haven.core.time import require_aware_utc
from haven.execution import ExecutionAdapter, ExecutionProviderRegistry, UnknownExecutionProvider
from haven.integrations.home_assistant.adapter import HomeAssistantAdapter
from haven.intelligence.gateway import IntelligenceProvider


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class AmbiguousTargetError(ValueError):
    """Raised when a direct action's selector resolves to more than one device.

    A direct action addresses exactly one device; a selector matching
    several is a clarification for the requester, not a fan-out (group
    fan-out is what approved selector-based rules are for).
    """


def _decision(
    status: DecisionStatus,
    code: DecisionCode,
    explanation: str,
    *,
    required_role: RoleTier | None = None,
) -> AuthorityDecision:
    return AuthorityDecision(status, code, explanation, required_role=required_role)


@dataclass(frozen=True)
class RuleApprovalResult:
    rule: Rule
    decision: AuthorityDecision
    event: DomainEvent


@dataclass(frozen=True)
class RuleClarificationResult:
    rule: Rule
    decision: AuthorityDecision
    event: DomainEvent


class HavenRuntime:
    """Keep interpretation proposals, authority, execution, and receipts separate.

    The intelligence provider is proposal-only: it never receives the store,
    execution adapters, or the device registry.
    """

    def __init__(
        self,
        *,
        store: HavenStore,
        intelligence_provider: IntelligenceProvider | None = None,
        home_assistant: HomeAssistantAdapter | None = None,
        authority: AuthorityEngine | None = None,
        execution_providers: ExecutionProviderRegistry | None = None,
    ) -> None:
        if home_assistant is None and execution_providers is None:
            raise ValueError("HavenRuntime requires home_assistant, execution_providers, or both")
        if intelligence_provider is None:
            raise ValueError("HavenRuntime requires an intelligence_provider")
        self.store = store
        self.intelligence_provider = intelligence_provider
        self.home_assistant = home_assistant
        self.authority = authority or AuthorityEngine()
        self.execution_providers = execution_providers

    def propose_from_text(self, text: str, *, principal: Principal, now: datetime) -> Rule:
        draft = self.intelligence_provider.interpret(text, principal=principal, now=now)
        return self.propose_draft(draft, principal=principal, now=now)

    def propose_draft(self, draft: RuleDraft, *, principal: Principal, now: datetime) -> Rule:
        if draft.household_id != principal.household_id:
            raise ValueError("a proposal must be created in the principal's household")
        if draft.proposed_by != principal.actor_id:
            raise ValueError("a proposal actor must match the principal")
        rule = Rule(rule_id=_new_id("rule"), draft=draft)
        self.store.execute_transition(
            Transition(
                kind=TransitionKind.PROPOSE_RULE,
                household_id=self.store.household_id,
                actor_id=principal.actor_id,
                payload=rule,
                correlation_id=rule.rule_id,
            ),
            now=now,
        )
        return rule

    def clarify_rule(
        self,
        rule_id: str,
        draft: RuleDraft,
        *,
        principal: Principal,
        justification: str,
        now: datetime,
    ) -> RuleClarificationResult:
        now = require_aware_utc(now, name="clarification time")
        rule = self.store.get_rule(rule_id)
        if principal.household_id != rule.draft.household_id or draft.household_id != principal.household_id:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.CROSS_HOUSEHOLD,
                "the clarifying principal and draft must belong to the rule household",
            )
        elif principal.role_tier < RoleTier.MEMBER:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.WRONG_ROLE_TIER,
                "a household member or higher role is required to clarify a rule",
                required_role=RoleTier.MEMBER,
            )
        elif not justification.strip():
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.MISSING_JUSTIFICATION,
                "rule clarification requires a non-empty justification",
            )
        elif rule.status != RuleStatus.PROPOSED:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.INVALID_STATE_TRANSITION,
                "only a proposed rule can be clarified",
            )
        elif draft.proposed_by != rule.draft.proposed_by or draft.draft_id == rule.draft.draft_id:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.CLARIFICATION_MISMATCH,
                "clarification must retain the original proposer and use a new draft identity",
            )
        else:
            decision = _decision(
                DecisionStatus.ALLOW,
                DecisionCode.ALLOWED,
                "the scoped clarification is valid for owner review",
            )

        if decision.status != DecisionStatus.ALLOW:
            event = self.store.execute_transition(
                Transition(
                    kind=TransitionKind.RECORD_RULE_DECISION,
                    household_id=self.store.household_id,
                    actor_id=principal.actor_id,
                    payload=RuleDecision(
                        rule_id=rule_id,
                        decision=decision,
                        justification=justification,
                        blocked_event_type=EventType.RULE_CLARIFICATION_BLOCKED,
                    ),
                    correlation_id=rule_id,
                ),
                now=now,
            )
            return RuleClarificationResult(rule=self.store.get_rule(rule_id), decision=decision, event=event)

        event = self.store.execute_transition(
            Transition(
                kind=TransitionKind.CLARIFY_RULE,
                household_id=self.store.household_id,
                actor_id=principal.actor_id,
                payload=RuleClarification(
                    rule_id=rule_id,
                    draft=draft,
                    clarified_by=principal.actor_id,
                    justification=justification,
                ),
                correlation_id=rule_id,
            ),
            now=now,
        )
        return RuleClarificationResult(rule=self.store.get_rule(rule_id), decision=decision, event=event)

    def approve_rule(
        self,
        rule_id: str,
        *,
        principal: Principal,
        justification: str,
        now: datetime,
    ) -> RuleApprovalResult:
        now = require_aware_utc(now, name="approval time")
        rule = self.store.get_rule(rule_id)
        if principal.household_id != rule.draft.household_id:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.CROSS_HOUSEHOLD,
                "the approving principal must belong to the rule household",
            )
        elif principal.role_tier < RoleTier.OWNER:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.WRONG_ROLE_TIER,
                "only a household owner can approve an autonomous rule",
                required_role=RoleTier.OWNER,
            )
        elif not justification.strip():
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.MISSING_JUSTIFICATION,
                "rule approval requires a non-empty justification",
            )
        elif rule.status != RuleStatus.PROPOSED:
            decision = _decision(
                DecisionStatus.DENY,
                DecisionCode.INVALID_STATE_TRANSITION,
                "only a proposed rule can be approved",
            )
        elif rule.draft.unresolved:
            decision = _decision(
                DecisionStatus.NEEDS_CLARIFICATION,
                DecisionCode.NEEDS_CLARIFICATION,
                "the interpretation must be clarified before approval",
            )
        else:
            decision = _decision(
                DecisionStatus.ALLOW,
                DecisionCode.ALLOWED,
                "owner approval is valid for this scoped rule",
            )

        if decision.status != DecisionStatus.ALLOW:
            event = self.store.execute_transition(
                Transition(
                    kind=TransitionKind.RECORD_RULE_DECISION,
                    household_id=self.store.household_id,
                    actor_id=principal.actor_id,
                    payload=RuleDecision(rule_id=rule_id, decision=decision, justification=justification),
                    correlation_id=rule_id,
                ),
                now=now,
            )
            return RuleApprovalResult(rule=self.store.get_rule(rule_id), decision=decision, event=event)

        event = self.store.execute_transition(
            Transition(
                kind=TransitionKind.APPROVE_RULE,
                household_id=self.store.household_id,
                actor_id=principal.actor_id,
                payload=RuleApproval(
                    rule_id=rule_id,
                    approved_by=principal.actor_id,
                    approved_by_role=principal.role_tier,
                    justification=justification,
                ),
                correlation_id=rule_id,
            ),
            now=now,
        )
        return RuleApprovalResult(rule=self.store.get_rule(rule_id), decision=decision, event=event)

    def run_rule(
        self,
        rule_id: str,
        *,
        principal: Principal,
        world: WorldSnapshot,
        justification: str,
        now: datetime,
        confirmation_token: ConfirmationToken | None = None,
    ) -> ActionReceipt:
        """Run a single-device rule against its one `target_device_id`.

        A selector-based rule has no single device to run this way; use
        `run_rule_for_group()` instead.
        """

        rule = self.store.get_rule(rule_id)
        if rule.draft.target_device_id is None:
            raise ValueError(
                f"rule {rule_id} targets a device selector, not one device_id; "
                "use run_rule_for_group() instead"
            )
        return self._execute_against_device(
            rule,
            rule.draft.target_device_id,
            principal=principal,
            world=world,
            justification=justification,
            now=now,
            confirmation_token=confirmation_token,
        )

    def run_rule_for_group(
        self,
        rule_id: str,
        *,
        principal: Principal,
        world: WorldSnapshot,
        justification: str,
        now: datetime,
    ) -> tuple[ActionReceipt, ...]:
        """Run a selector-based rule once per device it currently resolves to.

        Each resolved device gets its own independent request, decision, and
        receipt -- a GUARDED device blocking on confirmation does not hold up
        a LOW_RISK device in the same group, and vice versa. Confirmation
        tokens are not supported here yet: a resolved device with a
        CONFIRMATION_REQUIRED capability will come back blocked, the same as
        calling `run_rule()` with no token.
        """

        rule = self.store.get_rule(rule_id)
        if rule.draft.target_selector is None:
            raise ValueError(f"rule {rule_id} targets one device_id, not a selector; use run_rule() instead")
        registry = self.authority.device_registry
        device_ids = registry.resolve(rule.draft.target_selector) if registry is not None else ()
        return tuple(
            self._execute_against_device(
                rule,
                device_id,
                principal=principal,
                world=world,
                justification=justification,
                now=now,
                confirmation_token=None,
            )
            for device_id in device_ids
        )

    def _execute_against_device(
        self,
        rule: Rule,
        target_device_id: str,
        *,
        principal: Principal,
        world: WorldSnapshot,
        justification: str,
        now: datetime,
        confirmation_token: ConfirmationToken | None,
    ) -> ActionReceipt:
        now = require_aware_utc(now, name="action time")
        request_id = confirmation_token.request_id if confirmation_token is not None else _new_id("request")
        request = ActionRequest(
            request_id=request_id,
            household_id=self.store.household_id,
            requested_by=principal.actor_id,
            rule_id=rule.rule_id,
            action_kind=rule.draft.action_kind,
            target_device_id=target_device_id,
            parameters=rule.draft.parameters,
            justification=justification,
            evidence_snapshot_id=world.snapshot_id,
            requested_at=now,
            origin=ActionOrigin.RULE,
            confirmation_token=confirmation_token,
            capability=rule.draft.capability,
        )
        decision = self.authority.decide(
            request,
            principal=principal,
            rule=rule,
            world=world,
            now=now,
            confirmation_consumed=(
                confirmation_token is not None
                and self.store.is_confirmation_consumed(confirmation_token.token_id)
            ),
        )
        evidence = (
            world.evidence_for_rule(rule.draft, target_device_id=target_device_id, at=now)
            if world.household_id == self.store.household_id and principal.household_id == self.store.household_id
            else ()
        )
        if decision.status != DecisionStatus.ALLOW:
            return self._record_blocked(
                request=request,
                interpretation=rule.draft.interpretation,
                evidence=evidence,
                decision=decision,
                correlation_id=rule.rule_id,
                principal=principal,
                now=now,
            )
        return self._record_executed(
            request=request,
            interpretation=rule.draft.interpretation,
            evidence=evidence,
            decision=decision,
            service=self._service_for_device(rule.draft, target_device_id),
            correlation_id=rule.rule_id,
            principal=principal,
            now=now,
        )

    def run_action(
        self,
        *,
        principal: Principal,
        action_kind: ActionKind,
        target_device_id: str | None = None,
        target_selector: DeviceSelector | None = None,
        parameters: tuple[tuple[str, Any], ...] = (),
        justification: str,
        world: WorldSnapshot,
        now: datetime,
        confirmation_token: ConfirmationToken | None = None,
        capability: str | None = None,
    ) -> ActionReceipt:
        """Execute one HUMAN-INITIATED action straight through authority.

        A member's direct command is the authorization; automation needs a
        rule, a command does not -- so no rule is proposed, approved, or
        stored, and `AuthorityEngine.decide_direct()` (not `decide()`)
        judges the request. The built `ActionRequest` is marked
        `origin=ActionOrigin.DIRECT`: origin is the typed seam between
        human-command authorization and automation authorization, and the
        store's AUTHORIZE gate branches on it rather than on any string
        sentinel. `ActionRequest.rule_id` must still be non-empty, so it
        carries the request's own id as an honest correlation key: no rule
        with that id exists or is created, and the receipt's origin is the
        origin field, never the rule_id's spelling. The receipt's
        interpretation is the justification itself -- for a direct command
        there is no draft interpretation, and the human's stated reason is
        exactly what was understood. Evidence is empty for the same reason:
        the human asserted the command, so nothing is inferred from world
        evidence.

        Exactly one of `target_device_id` / `target_selector` must be set.
        A selector that resolves to zero devices raises `ValueError` naming
        it; one that resolves to more than one raises `AmbiguousTargetError`
        with the resolved count, since a direct action addresses one device
        rather than fanning out.

        `capability`, like `RuleDraft.capability`, routes risk and service
        resolution through the target device's own manifest instead of the
        closed `ActionKind` set -- the direct-command analogue of
        `_service_for_device`, so a device whose capability has no
        dedicated `ActionKind` (a switch or fan's plain "power") is still
        commandable without inventing one.
        """

        now = require_aware_utc(now, name="action time")
        if (target_device_id is None) == (target_selector is None):
            raise ValueError("a direct action must set exactly one of target_device_id or target_selector")
        if target_selector is not None:
            target_device_id = self._resolve_direct_target(target_selector)
        request_id = confirmation_token.request_id if confirmation_token is not None else _new_id("request")
        # No rule exists for a direct action; the request's own id is the
        # honest correlation key for receipts, events, and confirmation
        # binding, and origin (never this string) carries the provenance.
        rule_id = request_id
        request = ActionRequest(
            request_id=request_id,
            household_id=self.store.household_id,
            requested_by=principal.actor_id,
            rule_id=rule_id,
            action_kind=action_kind,
            target_device_id=target_device_id,
            parameters=parameters,
            justification=justification,
            evidence_snapshot_id=world.snapshot_id,
            requested_at=now,
            origin=ActionOrigin.DIRECT,
            confirmation_token=confirmation_token,
            capability=capability,
        )
        decision = self.authority.decide_direct(
            request,
            principal=principal,
            world=world,
            now=now,
            confirmation_consumed=(
                confirmation_token is not None
                and self.store.is_confirmation_consumed(confirmation_token.token_id)
            ),
        )
        if decision.status != DecisionStatus.ALLOW:
            return self._record_blocked(
                request=request,
                interpretation=justification,
                evidence=(),
                decision=decision,
                correlation_id=rule_id,
                principal=principal,
                now=now,
            )
        return self._record_executed(
            request=request,
            interpretation=justification,
            evidence=(),
            decision=decision,
            service=self._resolve_service(capability=capability, action_kind=action_kind, target_device_id=target_device_id),
            correlation_id=rule_id,
            principal=principal,
            now=now,
        )

    def _resolve_direct_target(self, selector: DeviceSelector) -> str:
        """Resolve a direct action's selector to its one device, or raise."""

        registry = self.authority.device_registry
        resolved = registry.resolve(selector) if registry is not None else ()
        if not resolved:
            raise ValueError(f"no registered device matched selector {selector!r}")
        if len(resolved) > 1:
            raise AmbiguousTargetError(
                f"selector {selector!r} resolved to {len(resolved)} devices "
                f"{resolved!r}; a direct action addresses exactly one"
            )
        return resolved[0]

    def _record_blocked(
        self,
        *,
        request: ActionRequest,
        interpretation: str,
        evidence: tuple[EvidenceRef, ...],
        decision: AuthorityDecision,
        correlation_id: str,
        principal: Principal,
        now: datetime,
    ) -> ActionReceipt:
        """Record one blocked action and return its receipt.

        Shared by the rule and direct paths so both record the same
        BLOCK event and return the same receipt shape.
        """

        blocked = ActionRecord(
            action_id=_new_id("action"),
            request=request,
            status=ActionStatus.BLOCKED,
            decision=decision,
        )
        event = self.store.execute_transition(
            Transition(
                kind=TransitionKind.RECORD_BLOCK,
                household_id=self.store.household_id,
                actor_id=principal.actor_id,
                payload=blocked,
                correlation_id=correlation_id,
            ),
            now=now,
        )
        return ActionReceipt(
            receipt_id=_new_id("receipt"),
            requested_action=request,
            interpretation=interpretation,
            evidence=evidence,
            decision=decision,
            device_result=None,
            event_ids=(event.event_id,),
        )

    def _record_executed(
        self,
        *,
        request: ActionRequest,
        interpretation: str,
        evidence: tuple[EvidenceRef, ...],
        decision: AuthorityDecision,
        service: str,
        correlation_id: str,
        principal: Principal,
        now: datetime,
    ) -> ActionReceipt:
        """Authorize, execute, and record one allowed action.

        Shared by the rule and direct paths: AUTHORIZE_ACTION (which also
        consumes a bound confirmation token), one device command through
        the routed execution adapter, RECORD_EXECUTION, and the receipt.
        """

        authorized = ActionRecord(
            action_id=_new_id("action"),
            request=request,
            status=ActionStatus.AUTHORIZED,
            decision=decision,
        )
        authorized_event = self.store.execute_transition(
            Transition(
                kind=TransitionKind.AUTHORIZE_ACTION,
                household_id=self.store.household_id,
                actor_id=principal.actor_id,
                payload=authorized,
                correlation_id=correlation_id,
            ),
            now=now,
        )

        command = DeviceCommand(
            request_id=request.request_id,
            target_device_id=request.target_device_id,
            service=service,
            parameters=request.parameters,
            requested_at=now,
        )
        try:
            adapter = self._execution_adapter_for(request.target_device_id)
            result = adapter.execute(command)
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            result = self._integration_failure(exc, at=now)
        executed = replace(authorized, status=ActionStatus.EXECUTED, executed_at=now, result=result)
        executed_event = self.store.execute_transition(
            Transition(
                kind=TransitionKind.RECORD_EXECUTION,
                household_id=self.store.household_id,
                actor_id=principal.actor_id,
                payload=executed,
                correlation_id=correlation_id,
            ),
            now=now,
        )
        return ActionReceipt(
            receipt_id=_new_id("receipt"),
            requested_action=request,
            interpretation=interpretation,
            evidence=evidence,
            decision=decision,
            device_result=result,
            event_ids=(authorized_event.event_id, executed_event.event_id),
        )

    def _execution_adapter_for(self, target_device_id: str) -> ExecutionAdapter:
        """Route to the adapter for this device's provider_id, or the default.

        A device with a manifest registered in `self.authority.device_registry`
        routes through `self.execution_providers` by its declared
        `provider_id` -- this is the piece that lets a washer, a camera, and
        an IR blaster each execute through a different real integration
        while sharing one authority and receipt path. A device with no
        manifest, or a deployment that never configured
        `execution_providers`, falls back to `self.home_assistant`: nothing
        about this routing is required to use Haven.
        """

        registry = self.authority.device_registry
        if self.execution_providers is not None and registry is not None and registry.is_registered(target_device_id):
            provider_id = registry.get(target_device_id).provider_id
            if self.execution_providers.is_registered(provider_id):
                return self.execution_providers.get(provider_id)
        if self.home_assistant is not None:
            return self.home_assistant
        raise UnknownExecutionProvider(
            f"no execution provider is registered for device {target_device_id!r}, "
            "and no default home_assistant adapter was configured"
        )

    def _service_for_device(self, draft: RuleDraft, target_device_id: str) -> str:
        """Route an approved draft to a provider service for one device.

        A capability draft is routed through the device manifest that
        `AuthorityEngine` already used to authorize it -- by the time this
        runs, `decide()` has already returned ALLOW for this same device and
        `draft.capability` against `self.authority.device_registry`, so the
        device, capability, and its declared `service` are known to resolve.
        `target_device_id` is passed explicitly rather than read from
        `draft.target_device_id`, since a selector-based draft has none of
        its own. A draft with no capability keeps the original ActionKind
        mapping, which does not vary per device.
        """

        return self._resolve_service(
            capability=draft.capability, action_kind=draft.action_kind, target_device_id=target_device_id
        )

    def _resolve_service(self, *, capability: str | None, action_kind: ActionKind, target_device_id: str) -> str:
        """Shared by the rule path (`_service_for_device`) and `run_action`:
        a named capability resolves through that device's own manifest;
        with none, the closed `ActionKind` -> service mapping applies."""

        if capability is None:
            return self._service_for(action_kind)
        manifest = self.authority.device_registry.get(target_device_id)
        return manifest.capability(capability).service

    @staticmethod
    def _service_for(action_kind) -> str:
        services = {
            "turn_light_off": "light.turn_off",
            "set_light_brightness": "light.turn_on",
            "activate_scene": "scene.turn_on",
            "set_thermostat": "climate.set_temperature",
            "open_garage": "cover.open_cover",
            "close_garage": "cover.close",
            "unlock_door": "lock.unlock",
            "purchase": "haven.purchase",
            "change_alarm": "alarm_control_panel.alarm",
        }
        return services.get(action_kind.value, "haven.unmapped")

    @staticmethod
    def _integration_failure(exc: Exception, *, at: datetime):
        from haven.core.domain import DeviceResult

        return DeviceResult(
            success=False,
            detail=f"integration_error:{type(exc).__name__}",
            observed_at=at,
            source="haven.runtime",
        )


__all__ = ["AmbiguousTargetError", "HavenRuntime", "RuleApprovalResult", "RuleClarificationResult"]
