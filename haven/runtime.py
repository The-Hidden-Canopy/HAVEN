"""Orchestration for the first HAVEN vertical slice."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import uuid4

from haven.audit.receipts import ActionReceipt
from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionRecord,
    ActionRequest,
    ActionStatus,
    AuthorityDecision,
    ConfirmationToken,
    DecisionCode,
    DecisionStatus,
    DeviceCommand,
    DomainEvent,
    EventType,
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
from haven.intelligence.gateway import ModelGateway


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


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
    """Keep model interpretation, authority, execution, and receipts separate."""

    def __init__(
        self,
        *,
        store: HavenStore,
        model_gateway: ModelGateway,
        home_assistant: HomeAssistantAdapter | None = None,
        authority: AuthorityEngine | None = None,
        execution_providers: ExecutionProviderRegistry | None = None,
    ) -> None:
        if home_assistant is None and execution_providers is None:
            raise ValueError("HavenRuntime requires home_assistant, execution_providers, or both")
        self.store = store
        self.model_gateway = model_gateway
        self.home_assistant = home_assistant
        self.authority = authority or AuthorityEngine()
        self.execution_providers = execution_providers

    def propose_from_text(self, text: str, *, principal: Principal, now: datetime) -> Rule:
        draft = self.model_gateway.interpret(text, principal=principal, now=now)
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
        action_id = _new_id("action")
        if decision.status != DecisionStatus.ALLOW:
            blocked = ActionRecord(
                action_id=action_id,
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
                    correlation_id=rule.rule_id,
                ),
                now=now,
            )
            return ActionReceipt(
                receipt_id=_new_id("receipt"),
                requested_action=request,
                interpretation=rule.draft.interpretation,
                evidence=evidence,
                decision=decision,
                device_result=None,
                event_ids=(event.event_id,),
            )

        authorized = ActionRecord(
            action_id=action_id,
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
                correlation_id=rule.rule_id,
            ),
            now=now,
        )

        command = DeviceCommand(
            request_id=request.request_id,
            target_device_id=request.target_device_id,
            service=self._service_for_device(rule.draft, target_device_id),
            parameters=request.parameters,
            requested_at=now,
        )
        try:
            adapter = self._execution_adapter_for(target_device_id)
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
                correlation_id=rule.rule_id,
            ),
            now=now,
        )
        return ActionReceipt(
            receipt_id=_new_id("receipt"),
            requested_action=request,
            interpretation=rule.draft.interpretation,
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

        if draft.capability is None:
            return self._service_for(draft.action_kind)
        manifest = self.authority.device_registry.get(target_device_id)
        return manifest.capability(draft.capability).service

    @staticmethod
    def _service_for(action_kind) -> str:
        services = {
            "turn_light_off": "light.turn_off",
            "set_light_brightness": "light.turn_on",
            "activate_scene": "scene.turn_on",
            "set_thermostat": "climate.set_temperature",
            "open_garage": "cover.open_cover",
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


__all__ = ["HavenRuntime", "RuleApprovalResult", "RuleClarificationResult"]
