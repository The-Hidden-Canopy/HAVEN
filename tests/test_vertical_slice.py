from dataclasses import replace
from datetime import datetime, timedelta, timezone

from haven.audit.receipts import ActionReceipt
from haven.core.domain import (
    ActionKind,
    ActionStatus,
    ContextState,
    DeviceState,
    EvidenceStatus,
    EventType,
    PresenceState,
    Principal,
    RoleTier,
    RuleDraft,
    RuleStatus,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime


UTC = timezone.utc
BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _principal(*, actor_id: str = "resident-1", household_id: str = "household-a", role: RoleTier = RoleTier.MEMBER):
    return Principal(actor_id=actor_id, household_id=household_id, role_tier=role)


def _explicit_draft(principal: Principal, *, action_kind: ActionKind = ActionKind.SET_LIGHT_BRIGHTNESS) -> RuleDraft:
    parameters = (("brightness_pct", 20),) if action_kind == ActionKind.SET_LIGHT_BRIGHTNESS else ()
    target = "bedroom_lights" if action_kind == ActionKind.SET_LIGHT_BRIGHTNESS else "garage"
    room = "bedroom" if action_kind == ActionKind.SET_LIGHT_BRIGHTNESS else "driveway"
    return RuleDraft(
        draft_id="draft-explicit",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="explicit fixture rule",
        interpretation="Use the explicit household action when the resident is present.",
        trigger_person_id=principal.actor_id,
        trigger_room_id=room,
        required_context="working_late" if room == "bedroom" else None,
        action_kind=action_kind,
        target_device_id=target,
        parameters=parameters,
    )


def _world(
    principal: Principal,
    *,
    now: datetime = BASE_TIME + timedelta(minutes=2),
    status: EvidenceStatus = EvidenceStatus.OBSERVED,
    active: bool = True,
) -> WorldSnapshot:
    room = "bedroom" if active and principal.actor_id == "resident-1" else "driveway"
    context = (
        ContextState(
            context_id="working_late",
            active=active,
            observed_at=BASE_TIME,
            source="fixture.context",
            status=status,
        ),
    )
    return WorldSnapshot(
        snapshot_id="snapshot-1",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id=room,
                present=active,
                observed_at=BASE_TIME,
                source="fixture.presence",
                status=status,
            ),
        ),
        contexts=context if room == "bedroom" else (),
        devices=(
            DeviceState(
                device_id="bedroom_lights" if room == "bedroom" else "garage",
                kind="light" if room == "bedroom" else "cover",
                room_id=room,
                is_on=True,
                brightness_pct=80 if room == "bedroom" else None,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
                status=status,
            ),
        ),
    )


def _runtime():
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
    )
    return runtime, store, adapter, principal, owner


def test_ambiguous_phrase_is_proposed_but_cannot_be_approved() -> None:
    runtime, store, adapter, resident, owner = _runtime()

    rule = runtime.propose_from_text(
        "When I'm working late, don't blast the bedroom lights when I walk in.",
        principal=resident,
        now=BASE_TIME,
    )
    result = runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Preserve my late-work lighting preference.",
        now=BASE_TIME + timedelta(minutes=1),
    )

    assert rule.status == RuleStatus.PROPOSED
    assert result.rule.status == RuleStatus.PROPOSED
    assert result.decision.code.value == "needs_clarification"
    assert adapter.commands == ()
    assert [event.event_type for event in store.events] == [
        EventType.RULE_PROPOSED,
        EventType.RULE_APPROVAL_BLOCKED,
    ]


def test_ambiguous_phrase_can_be_clarified_then_approved_and_executed() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    rule = runtime.propose_from_text(
        "When I'm working late, don't blast the bedroom lights when I walk in.",
        principal=resident,
        now=BASE_TIME,
    )
    clarified_draft = replace(
        rule.draft,
        draft_id="draft-clarified",
        source_text="When I'm working late, cap bedroom lights at 20% when I walk in.",
        interpretation=(
            "When the requester enters the bedroom while working_late is active, "
            "set bedroom lights to 20 percent."
        ),
        parameters=(("brightness_pct", 20),),
        unresolved=(),
    )

    clarification = runtime.clarify_rule(
        rule.rule_id,
        clarified_draft,
        principal=resident,
        justification="Clarify the phrase with the explicit brightness cap I intend.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    approval = runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Approve the clarified late-work lighting rule.",
        now=BASE_TIME + timedelta(minutes=2),
    )
    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident, now=BASE_TIME + timedelta(minutes=3)),
        justification="Apply the clarified and approved rule.",
        now=BASE_TIME + timedelta(minutes=3),
    )

    assert clarification.decision.code.value == "allowed"
    assert clarification.rule.draft.draft_id == "draft-clarified"
    assert rule.draft.unresolved  # the original object remains unchanged
    assert approval.decision.code.value == "allowed"
    assert receipt.outcome == "executed"
    assert len(adapter.commands) == 1
    assert [event.event_type for event in store.events] == [
        EventType.RULE_PROPOSED,
        EventType.RULE_CLARIFIED,
        EventType.RULE_APPROVED,
        EventType.ACTION_AUTHORIZED,
        EventType.ACTION_EXECUTED,
    ]
    clarification_payload = dict(store.events[1].payload)
    assert clarification_payload["previous_draft_id"] == rule.draft.draft_id
    assert clarification_payload["draft_id"] == "draft-clarified"
    assert dict(store.events[2].payload)["justification"] == "Approve the clarified late-work lighting rule."


def test_explicit_rule_runs_through_authority_and_receipt() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    draft = _explicit_draft(resident)
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    approval = runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="I explicitly approve the late-work bedroom light cap.",
        now=BASE_TIME + timedelta(minutes=1),
    )

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident),
        justification="Apply the previously approved late-work lighting rule.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert approval.decision.code.value == "allowed"
    assert isinstance(receipt, ActionReceipt)
    assert receipt.outcome == "executed"
    assert receipt.decision.code.value == "allowed"
    assert receipt.interpretation == draft.interpretation
    serialized = receipt.to_dict()
    assert serialized["schema_version"] == "0.1.0"
    assert serialized["authority_decision"]["status"] == "allow"
    assert serialized["execution"]["success"] is True
    assert serialized["requested_action"]["parameters"] == {"brightness_pct": 20}
    assert {ref.status for ref in receipt.evidence} == {EvidenceStatus.OBSERVED}
    assert len(adapter.commands) == 1
    assert adapter.commands[0].service == "light.turn_on"
    assert adapter.commands[0].parameters == (("brightness_pct", 20),)
    assert store.state.actions[0].status == ActionStatus.EXECUTED
    assert store.state.memory[0].source_event_id == approval.event.event_id
    approval_payload = dict(store.events[1].payload)
    assert approval_payload["justification"] == "I explicitly approve the late-work bedroom light cap."
    assert [event.event_type for event in store.events] == [
        EventType.RULE_PROPOSED,
        EventType.RULE_APPROVED,
        EventType.ACTION_AUTHORIZED,
        EventType.ACTION_EXECUTED,
    ]
    assert tuple(event.event_id for event in store.events[2:]) == receipt.event_ids
