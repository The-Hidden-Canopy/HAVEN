"""Transition-only state and append-only domain events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import uuid4

from haven.errors import InvalidTransition, ScopeViolation, StateConflict

from .domain import (
    ActionOrigin,
    ActionRecord,
    ActionStatus,
    AuthorityDecision,
    DecisionStatus,
    DomainEvent,
    EventType,
    MemoryEntry,
    Rule,
    RuleDraft,
    RuleStatus,
    RoleTier,
    Transition,
    TransitionKind,
)
from .time import require_aware_utc


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _payload(**values: object) -> tuple[tuple[str, object], ...]:
    return tuple(sorted(values.items(), key=lambda item: item[0]))


@dataclass(frozen=True)
class HavenState:
    """The publicly readable state is immutable; changes require a transition."""

    revision: int = 0
    rules: tuple[Rule, ...] = ()
    actions: tuple[ActionRecord, ...] = ()
    memory: tuple[MemoryEntry, ...] = ()
    consumed_confirmation_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleApproval:
    rule_id: str
    approved_by: str
    approved_by_role: RoleTier
    justification: str


@dataclass(frozen=True)
class RuleClarification:
    rule_id: str
    draft: RuleDraft
    clarified_by: str
    justification: str


@dataclass(frozen=True)
class RuleDecision:
    rule_id: str
    decision: AuthorityDecision
    justification: str
    blocked_event_type: EventType = EventType.RULE_APPROVAL_BLOCKED


class HavenStore:
    """Small append-only event store with a transition-only state reducer."""

    def __init__(self, *, household_id: str) -> None:
        if not household_id.strip():
            raise ValueError("household_id must be a non-empty string")
        self._household_id = household_id
        self._state = HavenState()
        self._events: tuple[DomainEvent, ...] = ()

    @property
    def household_id(self) -> str:
        return self._household_id

    @property
    def state(self) -> HavenState:
        return self._state

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        return self._events

    def restore_rules(self, rules: tuple[Rule, ...]) -> None:
        """Rehydrate automations saved by a previous process lifetime.

        This is the ONLY non-transition mutation of the store, and it exists
        solely at the process-lifetime boundary: rehydration replays NO
        transitions and emits NO domain events, because the transitions that
        proposed and approved these rules already happened in a previous
        process lifetime. Replaying them here would fabricate activity this
        process never governed. The rules enter as already-accepted state --
        exactly what a restarted household means by "my automations survived
        the reboot" -- scoped and id-checked like any other rule admission.
        """

        rules = tuple(rules)
        for rule in rules:
            if rule.draft.household_id != self._household_id:
                raise ScopeViolation("restored rule household does not match this store")
        rule_ids = [rule.rule_id for rule in rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise InvalidTransition("restored rules contain duplicate rule_ids")
        self._state = replace(self._state, rules=rules)

    def get_rule(self, rule_id: str) -> Rule:
        for rule in self._state.rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(rule_id)

    def get_action(self, action_id: str) -> ActionRecord:
        for action in self._state.actions:
            if action.action_id == action_id:
                return action
        raise KeyError(action_id)

    def is_confirmation_consumed(self, token_id: str) -> bool:
        return token_id in self._state.consumed_confirmation_tokens

    def append_domain_event(self, event: DomainEvent) -> None:
        """Append one scoped event through the approved audit boundary."""

        if event.household_id != self._household_id:
            raise ScopeViolation("domain event household does not match this store")
        if any(existing.event_id == event.event_id for existing in self._events):
            raise ValueError(f"duplicate domain event: {event.event_id}")
        self._events = (*self._events, event)

    def execute_transition(
        self,
        transition: Transition,
        *,
        now: datetime,
        expected_revision: int | None = None,
    ) -> DomainEvent:
        """Reduce one valid transition and emit its domain event.

        There is intentionally no public ``add_rule`` or ``update_action``
        method. Domain state is replaced only after the corresponding event is
        accepted by ``append_domain_event``.
        """

        now = require_aware_utc(now, name="transition time")
        if expected_revision is not None and expected_revision != self._state.revision:
            raise StateConflict(
                f"expected revision {expected_revision}, current revision {self._state.revision}"
            )
        if transition.household_id != self._household_id:
            raise ScopeViolation("transition household does not match this store")

        event_id = _new_id("event")
        next_state, event_type, event_payload = self._reduce(transition, now=now, event_id=event_id)
        event = DomainEvent(
            event_id=event_id,
            household_id=self._household_id,
            event_type=event_type,
            actor_id=transition.actor_id,
            occurred_at=now,
            payload=event_payload,
            correlation_id=transition.correlation_id,
            source="haven.core.store",
        )
        self.append_domain_event(event)
        self._state = replace(next_state, revision=self._state.revision + 1)
        return event

    def _reduce(
        self,
        transition: Transition,
        *,
        now: datetime,
        event_id: str,
    ) -> tuple[HavenState, EventType, tuple[tuple[str, object], ...]]:
        state = self._state

        if transition.kind == TransitionKind.PROPOSE_RULE:
            rule = transition.payload
            if not isinstance(rule, Rule):
                raise TypeError("PROPOSE_RULE requires a Rule")
            if rule.draft.household_id != self._household_id:
                raise ScopeViolation("rule household does not match this store")
            if rule.status != RuleStatus.PROPOSED:
                raise InvalidTransition("only proposed rules can enter the store")
            if any(existing.rule_id == rule.rule_id for existing in state.rules):
                raise InvalidTransition(f"rule already exists: {rule.rule_id}")
            return (
                replace(state, rules=(*state.rules, rule)),
                EventType.RULE_PROPOSED,
                _payload(
                    rule_id=rule.rule_id,
                    draft_id=rule.draft.draft_id,
                    interpretation=rule.draft.interpretation,
                    unresolved=rule.draft.unresolved,
                ),
            )

        if transition.kind == TransitionKind.CLARIFY_RULE:
            clarification = transition.payload
            if not isinstance(clarification, RuleClarification):
                raise TypeError("CLARIFY_RULE requires a RuleClarification")
            rule = self.get_rule(clarification.rule_id)
            if rule.status != RuleStatus.PROPOSED:
                raise InvalidTransition("only proposed rules can be clarified")
            if clarification.clarified_by != transition.actor_id:
                raise InvalidTransition("clarification actor does not match the transition actor")
            if clarification.draft.household_id != self._household_id:
                raise ScopeViolation("clarified rule household does not match this store")
            if clarification.draft.proposed_by != rule.draft.proposed_by:
                raise InvalidTransition("clarification cannot replace the original proposal actor")
            if clarification.draft.draft_id == rule.draft.draft_id:
                raise InvalidTransition("clarification must provide a new draft identity")
            clarified = replace(rule, draft=clarification.draft)
            rules = tuple(clarified if item.rule_id == rule.rule_id else item for item in state.rules)
            return (
                replace(state, rules=rules),
                EventType.RULE_CLARIFIED,
                _payload(
                    rule_id=rule.rule_id,
                    previous_draft_id=rule.draft.draft_id,
                    draft_id=clarification.draft.draft_id,
                    clarified_by=clarification.clarified_by,
                    justification=clarification.justification,
                    unresolved=clarification.draft.unresolved,
                ),
            )

        if transition.kind == TransitionKind.APPROVE_RULE:
            approval = transition.payload
            if not isinstance(approval, RuleApproval):
                raise TypeError("APPROVE_RULE requires a RuleApproval")
            rule = self.get_rule(approval.rule_id)
            if rule.status != RuleStatus.PROPOSED:
                raise InvalidTransition("only proposed rules can be approved")
            if approval.approved_by != transition.actor_id:
                raise InvalidTransition("approval actor does not match the transition actor")
            if rule.draft.unresolved:
                raise InvalidTransition("a rule with unresolved interpretation cannot be approved")
            if approval.approved_by_role != RoleTier.OWNER:
                raise InvalidTransition("only an owner can approve a household rule")
            approved = replace(
                rule,
                status=RuleStatus.APPROVED,
                approved_by=approval.approved_by,
                approved_by_role=approval.approved_by_role,
                approved_at=now,
            )
            memory = MemoryEntry(
                entry_id=_new_id("memory"),
                household_id=self._household_id,
                kind="approved_rule",
                content=rule.draft.interpretation,
                source_event_id=event_id,
                recorded_at=now,
            )
            rules = tuple(approved if item.rule_id == rule.rule_id else item for item in state.rules)
            return (
                replace(state, rules=rules, memory=(*state.memory, memory)),
                EventType.RULE_APPROVED,
                _payload(
                    rule_id=rule.rule_id,
                    approved_by=approval.approved_by,
                    justification=approval.justification,
                    memory_id=memory.entry_id,
                ),
            )

        if transition.kind == TransitionKind.AUTHORIZE_ACTION:
            action = transition.payload
            if not isinstance(action, ActionRecord):
                raise TypeError("AUTHORIZE_ACTION requires an ActionRecord")
            if action.request.household_id != self._household_id:
                raise ScopeViolation("action household does not match this store")
            if action.status != ActionStatus.AUTHORIZED or action.decision.status != DecisionStatus.ALLOW:
                raise InvalidTransition("only allowed actions can be authorized")
            # The approved-rule gate is origin-typed, never string-matched:
            # provenance is declared on the request, not inferred from an
            # id's spelling. A DIRECT action names no rule -- the authority
            # engine already authorized it via decide_direct() -- so there
            # is no approved-rule gate to pass. A RULE action fails closed
            # on any rule id the store does not hold as APPROVED.
            if action.request.origin is ActionOrigin.RULE:
                try:
                    rule = self.get_rule(action.request.rule_id)
                except KeyError:
                    rule = None
                if rule is None or rule.status != RuleStatus.APPROVED:
                    raise InvalidTransition("an action requires an approved rule")
            if any(existing.action_id == action.action_id for existing in state.actions):
                raise InvalidTransition(f"action already exists: {action.action_id}")
            token = action.request.confirmation_token
            if token is not None and token.token_id in state.consumed_confirmation_tokens:
                raise InvalidTransition(f"confirmation token already consumed: {token.token_id}")
            consumed_tokens = state.consumed_confirmation_tokens
            if token is not None:
                consumed_tokens = (*consumed_tokens, token.token_id)
            return (
                replace(
                    state,
                    actions=(*state.actions, action),
                    consumed_confirmation_tokens=consumed_tokens,
                ),
                EventType.ACTION_AUTHORIZED,
                _payload(
                    action_id=action.action_id,
                    request_id=action.request.request_id,
                    rule_id=action.request.rule_id,
                    target_device_id=action.request.target_device_id,
                ),
            )

        if transition.kind == TransitionKind.RECORD_EXECUTION:
            executed = transition.payload
            if not isinstance(executed, ActionRecord):
                raise TypeError("RECORD_EXECUTION requires an ActionRecord")
            if executed.status != ActionStatus.EXECUTED:
                raise InvalidTransition("execution records must have EXECUTED status")
            current = self.get_action(executed.action_id)
            if current.status != ActionStatus.AUTHORIZED:
                raise InvalidTransition("only authorized actions can cross the execution boundary")
            if current.request != executed.request:
                raise InvalidTransition("execution request does not match the authorized request")
            actions = tuple(executed if item.action_id == current.action_id else item for item in state.actions)
            return (
                replace(state, actions=actions),
                EventType.ACTION_EXECUTED,
                _payload(
                    action_id=executed.action_id,
                    request_id=executed.request.request_id,
                    success=executed.result.success if executed.result is not None else False,
                    result_source=executed.result.source if executed.result is not None else "missing",
                ),
            )

        if transition.kind == TransitionKind.RECORD_BLOCK:
            blocked = transition.payload
            if not isinstance(blocked, ActionRecord):
                raise TypeError("RECORD_BLOCK requires an ActionRecord")
            if blocked.status != ActionStatus.BLOCKED:
                raise InvalidTransition("blocked records must have BLOCKED status")
            if blocked.decision.status == DecisionStatus.ALLOW:
                raise InvalidTransition("an allowed action cannot be recorded as blocked")
            if any(existing.action_id == blocked.action_id for existing in state.actions):
                raise InvalidTransition(f"action already exists: {blocked.action_id}")
            return (
                replace(state, actions=(*state.actions, blocked)),
                EventType.ACTION_BLOCKED,
                _payload(
                    action_id=blocked.action_id,
                    request_id=blocked.request.request_id,
                    code=blocked.decision.code.value,
                    status=blocked.decision.status.value,
                ),
            )

        if transition.kind == TransitionKind.RECORD_RULE_DECISION:
            decision = transition.payload
            if not isinstance(decision, RuleDecision):
                raise TypeError("RECORD_RULE_DECISION requires a RuleDecision")
            if decision.decision.status == DecisionStatus.ALLOW:
                raise InvalidTransition("allowed rule decisions do not belong in the blocked ledger")
            if decision.blocked_event_type not in {
                EventType.RULE_APPROVAL_BLOCKED,
                EventType.RULE_CLARIFICATION_BLOCKED,
            }:
                raise InvalidTransition("rule decision event type is not a blocked rule event")
            self.get_rule(decision.rule_id)
            return (
                state,
                decision.blocked_event_type,
                _payload(
                    rule_id=decision.rule_id,
                    code=decision.decision.code.value,
                    status=decision.decision.status.value,
                    justification=decision.justification,
                ),
            )

        raise InvalidTransition(f"unsupported transition: {transition.kind}")


__all__ = ["HavenState", "HavenStore", "RuleApproval", "RuleClarification", "RuleDecision"]
