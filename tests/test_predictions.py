"""Prediction-triggered rules: predictions become inputs to routines.

Nothing in Haven Core produces a `Prediction` yet -- this is the contract a
prediction engine (a world model, Ghost Teacher) plugs into. A rule that
declares a `PredictionTrigger` is judged against the confidence bar *it*
declared at approval time, not against `AuthorityEngine.minimum_confidence`
(which governs observed evidence and is unrelated here) -- the ".75-.95 ->
execute only pre-approved predictions" tier is exactly an owner approving a
rule with that bar built in.
"""

from datetime import timedelta

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    DecisionCode,
    DeviceState,
    EvidenceStatus,
    Prediction,
    PredictionTrigger,
    RuleDraft,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, RoleTier, _principal


def _runtime():
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store, intelligence_provider=ScriptedIntelligenceProvider(), home_assistant=adapter, authority=AuthorityEngine()
    )
    return runtime, store, adapter, principal, owner


def _bedtime_draft(principal, *, min_confidence: float = 0.85, subject_id: str | None = "bedroom") -> RuleDraft:
    return RuleDraft(
        draft_id="draft-bedtime",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="when bedroom occupancy looks like bedtime, cool it down",
        interpretation="Set the bedroom thermostat to 68 when a bedtime prediction meets the approved bar.",
        action_kind=ActionKind.SET_THERMOSTAT,
        target_device_id="bedroom_thermostat",
        parameters=(("target_temperature", 68),),
        prediction_trigger=PredictionTrigger(
            event="bedroom_occupied_for_sleep", min_confidence=min_confidence, subject_id=subject_id
        ),
    )


def _world(*, predictions=(), household_id="household-a") -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="prediction-snapshot",
        household_id=household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(hours=1),
        devices=(
            DeviceState(
                device_id="bedroom_thermostat",
                kind="climate",
                room_id="bedroom",
                is_on=True,
                brightness_pct=None,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
            ),
        ),
        predictions=predictions,
    )


def _approve(runtime, draft, *, resident, owner):
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the prediction-triggered bedtime rule.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rule


def test_prediction_at_or_above_the_declared_bar_executes():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _bedtime_draft(resident), resident=resident, owner=owner)
    world = _world(
        predictions=(
            Prediction(
                event="bedroom_occupied_for_sleep",
                subject_id="bedroom",
                confidence=0.90,
                explanation="Continuous occupancy for 18 minutes, temperature climbing.",
                observed_at=BASE_TIME,
                source="ghost_teacher.world_model",
            ),
        )
    )

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=world,
        justification="Apply the approved bedtime prediction rule.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert len(adapter.commands) == 1
    prediction_evidence = [e for e in receipt.evidence if e.kind == "prediction"]
    assert len(prediction_evidence) == 1
    assert prediction_evidence[0].status == EvidenceStatus.DECLARED
    assert prediction_evidence[0].subject_id == "bedroom_occupied_for_sleep:bedroom"


def test_prediction_below_the_declared_bar_blocks():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _bedtime_draft(resident), resident=resident, owner=owner)
    world = _world(
        predictions=(
            Prediction(
                event="bedroom_occupied_for_sleep",
                subject_id="bedroom",
                confidence=0.62,
                explanation="Some occupancy signal, but short and inconsistent with prior evenings.",
                observed_at=BASE_TIME,
                source="ghost_teacher.world_model",
            ),
        )
    )

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=world,
        justification="Attempt to apply the bedtime rule below its confidence bar.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code == DecisionCode.LOW_CONFIDENCE_EVIDENCE
    assert adapter.commands == ()


def test_no_matching_prediction_blocks_as_evidence_missing():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _bedtime_draft(resident), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(),
        justification="No prediction has been made at all.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code == DecisionCode.EVIDENCE_MISSING
    assert adapter.commands == ()


def test_prediction_for_a_different_subject_does_not_match():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _bedtime_draft(resident), resident=resident, owner=owner)
    world = _world(
        predictions=(
            Prediction(
                event="bedroom_occupied_for_sleep",
                subject_id="guest_room",
                confidence=0.95,
                explanation="High-confidence prediction, but about the wrong room.",
                observed_at=BASE_TIME,
                source="ghost_teacher.world_model",
            ),
        )
    )

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=world,
        justification="A prediction exists but is not about this rule's subject.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code == DecisionCode.EVIDENCE_MISSING


def test_a_future_prediction_cannot_authorize_a_present_decision():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _bedtime_draft(resident), resident=resident, owner=owner)
    world = _world(
        predictions=(
            Prediction(
                event="bedroom_occupied_for_sleep",
                subject_id="bedroom",
                confidence=0.95,
                explanation="A prediction generated after the decision instant.",
                observed_at=BASE_TIME + timedelta(minutes=10),
                source="ghost_teacher.world_model",
            ),
        )
    )

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=world,
        justification="Decision time precedes the prediction's own timestamp.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code == DecisionCode.EVIDENCE_MISSING


def test_rule_draft_requires_exactly_one_of_presence_or_prediction_trigger():
    principal = _principal()
    base = dict(
        draft_id="draft-x",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text="x",
        interpretation="x",
        action_kind=ActionKind.SET_THERMOSTAT,
        target_device_id="bedroom_thermostat",
    )
    with pytest.raises(ValueError):
        RuleDraft(**base)  # neither a presence trigger nor a prediction_trigger
    with pytest.raises(ValueError):
        RuleDraft(
            **base,
            trigger_person_id=principal.actor_id,
            trigger_room_id="bedroom",
            prediction_trigger=PredictionTrigger(event="x", min_confidence=0.9),
        )
    with pytest.raises(ValueError):
        RuleDraft(**base, trigger_person_id=principal.actor_id)  # person without room


def test_world_snapshot_rejects_a_prediction_made_after_validity():
    with pytest.raises(ValueError):
        _world(
            predictions=(
                Prediction(
                    event="e",
                    subject_id="s",
                    confidence=0.9,
                    explanation="x",
                    observed_at=BASE_TIME + timedelta(hours=2),
                    source="ghost_teacher.world_model",
                ),
            )
        )


def test_prediction_confidence_must_be_between_zero_and_one():
    with pytest.raises(ValueError):
        Prediction(
            event="e", subject_id="s", confidence=1.2, explanation="x", observed_at=BASE_TIME, source="x"
        )
    with pytest.raises(ValueError):
        PredictionTrigger(event="e", min_confidence=-0.1)
