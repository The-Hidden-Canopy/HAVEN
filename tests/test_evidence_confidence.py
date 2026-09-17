"""Confidence-gated evidence: the seam for probabilistic perception.

Haven Core has no camera or IR pipeline; nothing in this repo produces a
sub-1.0 `confidence` value yet. This is the policy hook a future vision/IR
provider plugs into: it can report `PresenceState`/`ContextState`/
`DeviceState` with `confidence < 1.0`, and `AuthorityEngine` fails closed on
it by default -- exactly like it already fails closed on stale or
unavailable evidence -- until a household explicitly lowers
`minimum_confidence`.
"""

from datetime import timedelta

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import ContextState, DeviceState, PresenceState, WorldSnapshot
from test_vertical_slice import BASE_TIME, _explicit_draft, _principal, RoleTier, HavenStore
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime


def _runtime_with_confidence(minimum_confidence: float):
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
        authority=AuthorityEngine(minimum_confidence=minimum_confidence),
    )
    return runtime, store, adapter, principal, owner


def _world(principal, *, presence_confidence: float = 1.0) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="confidence-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id="bedroom",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.vision",
                confidence=presence_confidence,
            ),
        ),
        contexts=(
            ContextState(
                context_id="working_late",
                active=True,
                observed_at=BASE_TIME,
                source="fixture.context",
            ),
        ),
        devices=(
            DeviceState(
                device_id="bedroom_lights",
                kind="light",
                room_id="bedroom",
                is_on=True,
                brightness_pct=80,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
            ),
        ),
    )


def _approve(runtime, *, resident, owner):
    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the confidence-gated rule.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rule


def test_below_default_confidence_blocks_by_default():
    runtime, store, adapter, resident, owner = _runtime_with_confidence(1.0)
    rule = _approve(runtime, resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident, presence_confidence=0.6),
        justification="A vision-sourced presence observation with 60% confidence.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "low_confidence_evidence"
    assert adapter.commands == ()


def test_lowering_the_threshold_admits_the_same_observation():
    runtime, store, adapter, resident, owner = _runtime_with_confidence(0.5)
    rule = _approve(runtime, resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident, presence_confidence=0.6),
        justification="Same observation, household opted into a lower bar.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"


def test_full_confidence_evidence_is_unaffected_by_the_default_threshold():
    runtime, store, adapter, resident, owner = _runtime_with_confidence(1.0)
    rule = _approve(runtime, resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_world(resident, presence_confidence=1.0),
        justification="Ordinary ground-truth presence evidence.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"


def test_confidence_must_be_between_zero_and_one():
    with pytest.raises(ValueError):
        PresenceState(
            person_id="resident-1",
            room_id="bedroom",
            present=True,
            observed_at=BASE_TIME,
            source="fixture.vision",
            confidence=1.5,
        )
