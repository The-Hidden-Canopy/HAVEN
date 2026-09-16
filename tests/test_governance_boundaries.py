from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from haven.core.domain import (
    ActionKind,
    AuthorityDecision,
    ContextState,
    ConfirmationToken,
    DecisionCode,
    DecisionStatus,
    DeviceState,
    DomainEvent,
    EvidenceStatus,
    EventType,
    PresenceState,
    Principal,
    RoleTier,
    RuleStatus,
    Transition,
    TransitionKind,
    WorldSnapshot,
)
from haven.core.store import HavenStore, RuleApproval, RuleDecision
from haven.errors import InvalidTransition, ScopeViolation, StateConflict
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, _explicit_draft, _principal, _runtime, _world


UTC = timezone.utc


def _approved_fixture():
    runtime, store, adapter, resident, owner = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the explicit fixture rule.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return runtime, store, adapter, resident, owner, rule


def test_cross_household_approval_is_blocked_and_audited() -> None:
    runtime, store, _, resident, _ = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    foreign_owner = _principal(actor_id="foreign-owner", household_id="household-b", role=RoleTier.OWNER)

    result = runtime.approve_rule(
        rule.rule_id,
        principal=foreign_owner,
        justification="Attempted cross-household approval.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert result.decision.code.value == "cross_household"
    assert result.rule.status == RuleStatus.PROPOSED
    assert store.events[-1].event_type == EventType.RULE_APPROVAL_BLOCKED


def test_cross_household_action_does_not_join_foreign_evidence() -> None:
    runtime, store, adapter, _, _, rule = _approved_fixture()
    foreign_member = _principal(actor_id="foreign-member", household_id="household-b", role=RoleTier.MEMBER)
    foreign_world = _world(foreign_member)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=foreign_member,
        world=foreign_world,
        justification="Attempted cross-household execution.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "cross_household"
    assert receipt.evidence == ()
    assert adapter.commands == ()
    assert store.events[-1].event_type == EventType.ACTION_BLOCKED


def test_wrong_role_tier_and_missing_justification_are_blocked() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)

    wrong_role = runtime.approve_rule(
        rule.rule_id,
        principal=resident,
        justification="A member tries to approve.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    missing_justification = runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="   ",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert wrong_role.decision.code.value == "wrong_role_tier"
    assert missing_justification.decision.code.value == "missing_justification"
    assert store.get_rule(rule.rule_id).status == RuleStatus.PROPOSED
    assert adapter.commands == ()
    assert [event.event_type for event in store.events[-2:]] == [
        EventType.RULE_APPROVAL_BLOCKED,
        EventType.RULE_APPROVAL_BLOCKED,
    ]


def test_clarification_cannot_change_proposer_or_cross_household_scope() -> None:
    runtime, store, _, resident, _ = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    wrong_proposer = replace(
        rule.draft,
        draft_id="draft-wrong-proposer",
        proposed_by="different-resident",
        source_text="changed proposer",
        parameters=(("brightness_pct", 10),),
    )
    wrong_scope = replace(
        rule.draft,
        draft_id="draft-wrong-scope",
        household_id="household-b",
        proposed_by=resident.actor_id,
        source_text="foreign clarification",
        parameters=(("brightness_pct", 10),),
    )

    proposer_result = runtime.clarify_rule(
        rule.rule_id,
        wrong_proposer,
        principal=resident,
        justification="Attempt to replace the proposer.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    scope_result = runtime.clarify_rule(
        rule.rule_id,
        wrong_scope,
        principal=resident,
        justification="Attempt to import a foreign draft.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert proposer_result.decision.code.value == "clarification_mismatch"
    assert scope_result.decision.code.value == "cross_household"
    assert store.get_rule(rule.rule_id).draft.draft_id == rule.draft.draft_id
    assert [event.event_type for event in store.events[-2:]] == [
        EventType.RULE_CLARIFICATION_BLOCKED,
        EventType.RULE_CLARIFICATION_BLOCKED,
    ]


@pytest.mark.parametrize(
    "status",
    [EvidenceStatus.STALE, EvidenceStatus.FALLBACK, EvidenceStatus.DECLARED, EvidenceStatus.UNAVAILABLE],
)
def test_stale_fallback_declared_or_unavailable_evidence_cannot_authorize(status: EvidenceStatus) -> None:
    runtime, store, adapter, resident, _, rule = _approved_fixture()
    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident, status=status),
        justification="Apply the approved rule if current evidence supports it.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value in {"stale_evidence", "evidence_unavailable"}
    assert receipt.outcome == "unavailable"
    assert adapter.commands == ()
    assert store.events[-1].event_type == EventType.ACTION_BLOCKED


def test_expired_snapshot_is_not_treated_as_false_trigger() -> None:
    runtime, _, adapter, resident, _, rule = _approved_fixture()
    expired = _world(resident, now=BASE_TIME + timedelta(minutes=2))
    expired = WorldSnapshot(
        snapshot_id=expired.snapshot_id,
        household_id=expired.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=1),
        presence=expired.presence,
        contexts=expired.contexts,
        devices=expired.devices,
    )

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=expired,
        justification="Use only current evidence.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "stale_evidence"
    assert adapter.commands == ()


def test_missing_justification_blocks_action_even_with_fresh_evidence() -> None:
    runtime, _, adapter, resident, _, rule = _approved_fixture()

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident),
        justification="  ",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "missing_justification"
    assert adapter.commands == ()


def test_confirmation_required_action_needs_token_before_adapter_call() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    garage_rule = runtime.propose_draft(
        _explicit_draft(resident, action_kind=ActionKind.OPEN_GARAGE),
        principal=resident,
        now=BASE_TIME,
    )
    runtime.approve_rule(
        garage_rule.rule_id,
        principal=owner,
        justification="Explicitly approve the garage routine, subject to confirmation.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    world = _world(resident)
    world = WorldSnapshot(
        snapshot_id="garage-snapshot",
        household_id=resident.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=resident.actor_id,
                room_id="driveway",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.presence",
            ),
        ),
        devices=(
            DeviceState(
                device_id="garage",
                kind="cover",
                room_id="driveway",
                is_on=False,
                brightness_pct=None,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
            ),
        ),
    )

    blocked = runtime.run_rule(
        garage_rule.rule_id,
        principal=resident,
        world=world,
        justification="Open the approved garage routine.",
        now=BASE_TIME + timedelta(minutes=2),
    )
    token = ConfirmationToken(
        token_id="confirmation-1",
        household_id=resident.household_id,
        rule_id=garage_rule.rule_id,
        request_id=blocked.requested_action.request_id,
        confirmed_by=resident.actor_id,
        issued_at=BASE_TIME + timedelta(minutes=2),
        expires_at=BASE_TIME + timedelta(minutes=5),
    )
    allowed = runtime.run_rule(
        garage_rule.rule_id,
        principal=resident,
        world=world,
        justification="Open the approved garage routine after my confirmation.",
        confirmation_token=token,
        now=BASE_TIME + timedelta(minutes=3),
    )
    replayed = runtime.run_rule(
        garage_rule.rule_id,
        principal=resident,
        world=world,
        justification="Replay the same confirmation.",
        confirmation_token=token,
        now=BASE_TIME + timedelta(minutes=4),
    )

    assert blocked.decision.code.value == "confirmation_required"
    assert allowed.outcome == "executed"
    assert replayed.decision.code.value == "confirmation_reused"
    assert "confirmation-1" not in allowed.to_json()
    assert len(adapter.commands) == 1
    assert adapter.commands[0].service == "cover.open_cover"
    assert [event.event_type for event in store.events[-4:]] == [
        EventType.ACTION_BLOCKED,
        EventType.ACTION_AUTHORIZED,
        EventType.ACTION_EXECUTED,
        EventType.ACTION_BLOCKED,
    ]


def test_invalid_rule_transition_is_blocked_and_audited() -> None:
    runtime, store, _, resident, owner = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="First owner approval.",
        now=BASE_TIME + timedelta(minutes=1),
    )

    result = runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Invalid second approval attempt.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert result.decision.code.value == "invalid_state_transition"
    assert store.events[-1].event_type == EventType.RULE_APPROVAL_BLOCKED
    assert store.state.revision == len(store.events)


def test_state_is_immutable_and_background_mutation_has_no_event_path() -> None:
    runtime, store, _, resident, _ = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    events_before = store.events

    with pytest.raises(AttributeError):
        store.state.rules.append(rule)  # tuple: no direct state mutation API
    with pytest.raises(FrozenInstanceError):
        store.state.rules[0].status = RuleStatus.APPROVED

    assert store.events == events_before
    assert store.state.revision == 1


def test_direct_foreign_transition_and_stale_revision_are_rejected() -> None:
    runtime, store, _, resident, _ = _runtime()
    draft = _explicit_draft(resident)
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)

    with pytest.raises(ScopeViolation):
        store.execute_transition(
            Transition(
                kind=TransitionKind.PROPOSE_RULE,
                household_id="household-b",
                actor_id="foreign",
                payload=rule,
                correlation_id="foreign-transition",
            ),
            now=BASE_TIME + timedelta(minutes=1),
        )
    with pytest.raises(StateConflict):
        store.execute_transition(
            Transition(
                kind=TransitionKind.PROPOSE_RULE,
                household_id=store.household_id,
                actor_id=resident.actor_id,
                payload=rule,
                correlation_id="stale-transition",
            ),
            now=BASE_TIME + timedelta(minutes=1),
            expected_revision=0,
        )

    assert len(store.events) == 1


def test_transition_payload_cannot_forge_actor_or_blocked_event_type() -> None:
    runtime, store, _, resident, owner = _runtime()
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)

    with pytest.raises(InvalidTransition, match="approval actor"):
        store.execute_transition(
            Transition(
                kind=TransitionKind.APPROVE_RULE,
                household_id=store.household_id,
                actor_id=owner.actor_id,
                payload=RuleApproval(
                    rule_id=rule.rule_id,
                    approved_by="forged-approver",
                    approved_by_role=RoleTier.OWNER,
                    justification="Forged approval payload.",
                ),
                correlation_id=rule.rule_id,
            ),
            now=BASE_TIME + timedelta(minutes=1),
        )

    forged_decision = AuthorityDecision(
        status=DecisionStatus.DENY,
        code=DecisionCode.MISSING_JUSTIFICATION,
        explanation="fixture blocked decision",
    )
    with pytest.raises(InvalidTransition, match="blocked rule event"):
        store.execute_transition(
            Transition(
                kind=TransitionKind.RECORD_RULE_DECISION,
                household_id=store.household_id,
                actor_id=owner.actor_id,
                payload=RuleDecision(
                    rule_id=rule.rule_id,
                    decision=forged_decision,
                    justification="Forged event type.",
                    blocked_event_type=EventType.RULE_APPROVED,
                ),
                correlation_id=rule.rule_id,
            ),
            now=BASE_TIME + timedelta(minutes=2),
        )

    assert store.get_rule(rule.rule_id).status == RuleStatus.PROPOSED
    assert len(store.events) == 1


def test_naive_datetime_is_rejected_at_event_and_snapshot_boundaries() -> None:
    naive = datetime(2026, 9, 16, 20, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        DomainEvent(
            event_id="event-naive",
            household_id="household-a",
            event_type=EventType.RULE_PROPOSED,
            actor_id="resident-1",
            occurred_at=naive,
            payload=(),
            correlation_id="correlation-1",
            source="test",
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        WorldSnapshot(
            snapshot_id="snapshot-naive",
            household_id="household-a",
            captured_at=naive,
            valid_until=naive + timedelta(minutes=5),
        )
