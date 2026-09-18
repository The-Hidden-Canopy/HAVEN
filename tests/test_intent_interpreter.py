"""The deterministic intent interpreter and the interpret_intent seam.

Classification moved out of the demo and into the intelligence seam: the
interpreter turns one utterance into exactly ONE intent form, grounded
against a WorldView and a room focus, and `ScriptedIntelligenceProvider`
implements `interpret_intent` by delegating to it. The model bridge only
upgrades classification when a loaded handle exposes a capability-gated
structured_intent method whose payload validates as a complete intent.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.core.domain import (
    ActionKind,
    ChangeOrigin,
    DeviceState,
    Principal,
    RoleTier,
    RuleDraft,
    WorldSnapshot,
)
from haven.intelligence.gateway import AgentContext, ScriptedIntelligenceProvider
from haven.intelligence.intents import (
    ActionProposal,
    ClarificationRequest,
    ConversationMessage,
    QueryRequest,
)
from haven.intelligence.interpreter import DeterministicIntentInterpreter
from haven.intelligence.worldview import WorldView
from haven.models import ModelKind
from haven.models.bridge import ModelIntelligenceProvider
from test_models_bridge import (
    FakeBackend,
    ScriptedHandle,
    _install_local,
    _manager_with_backend,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
EXAMPLE = "When I'm working late, don't blast the bedroom lights when I walk in."
RESIDENT = Principal(actor_id="resident-1", household_id="household-1", role_tier=RoleTier.MEMBER)


def _world(*, garage_open: bool = True) -> WorldView:
    devices = [
        DeviceState(
            device_id="office_light",
            kind="light",
            room_id="office",
            is_on=True,
            brightness_pct=None,
            observed_at=NOW - timedelta(minutes=1),
            source="demo",
            changed_by=ChangeOrigin.SYSTEM,
        ),
        DeviceState(
            device_id="bedroom_desk_fan",
            kind="fan",
            room_id="bedroom",
            is_on=False,
            brightness_pct=None,
            observed_at=NOW - timedelta(minutes=1),
            source="demo",
            changed_by=ChangeOrigin.SYSTEM,
        ),
        DeviceState(
            device_id="garage_door",
            kind="cover",
            room_id="garage",
            is_on=garage_open,
            brightness_pct=None,
            observed_at=NOW - timedelta(minutes=18),
            source="demo",
            changed_by=ChangeOrigin.SYSTEM,
        ),
    ]
    snapshot = WorldSnapshot(
        snapshot_id="snap-1",
        household_id=RESIDENT.household_id,
        captured_at=NOW,
        valid_until=NOW + timedelta(minutes=30),
        devices=tuple(devices),
    )
    return WorldView.from_snapshot(snapshot, now=NOW)


def _classify(text: str, *, world: WorldView | None = None, room_focus: str | None = None):
    interpreter = DeterministicIntentInterpreter()
    return interpreter.classify(text, world=world, room_focus=room_focus)


def _fixture_drafter():
    provider = ScriptedIntelligenceProvider()

    def draft_for(text: str, principal: Principal, now: datetime) -> RuleDraft:
        return provider.interpret(text, principal=principal, now=now)

    return draft_for


# -- the classification table ---------------------------------------------------


def test_question_shapes_become_query_requests() -> None:
    for text in (
        "is the garage open?",
        "what time is it",
        "why is the sky blue?",
        "the garage is open?",
    ):
        intent = _classify(text)
        assert isinstance(intent, QueryRequest), text
        assert intent.text == text


def test_room_light_off_grounds_against_the_world() -> None:
    for text in (
        "turn off the office light",
        "turn the office light off",
        "turn off the light in the office",
        "turn off the the office light",  # "the office" tolerated
    ):
        intent = _classify(text, world=_world())
        assert isinstance(intent, ActionProposal), text
        assert intent.action_kind is ActionKind.TURN_LIGHT_OFF
        assert intent.target_device_id is None
        assert intent.target_selector is not None
        assert intent.target_selector.role == "light"
        assert intent.target_selector.room == "office"
        assert intent.justification == f"direct command: {text}"
        assert intent.parameters == ()
        assert intent.source_text == text


def test_room_without_a_light_falls_back_to_conversation() -> None:
    intent = _classify("turn off the bedroom light", world=_world())

    assert isinstance(intent, ConversationMessage)
    assert intent.text == "turn off the bedroom light"


def test_unknown_room_asks_for_clarification() -> None:
    intent = _classify("turn off the attic light", world=_world())

    assert isinstance(intent, ClarificationRequest)
    assert intent.question == "Which room do you mean?"
    assert intent.source_text == "turn off the attic light"


def test_bare_light_off_without_focus_asks_for_clarification() -> None:
    intent = _classify("turn that light off", world=_world())

    assert isinstance(intent, ClarificationRequest)
    assert intent.question == "Which room do you mean?"


def test_bare_light_off_resolves_against_the_focus() -> None:
    intent = _classify("turn that light off", world=_world(), room_focus="office")

    assert isinstance(intent, ActionProposal)
    assert intent.target_selector is not None
    assert intent.target_selector.room == "office"


def test_bare_light_off_with_focus_and_no_world_uses_the_focus() -> None:
    intent = _classify("turn that light off", room_focus="office")

    assert isinstance(intent, ActionProposal)
    assert intent.target_selector is not None
    assert intent.target_selector.room == "office"


def test_targeted_commands_without_world_or_focus_ask_for_clarification() -> None:
    for text in ("turn off the office light", "turn that light off"):
        intent = _classify(text)
        assert isinstance(intent, ClarificationRequest), text
        assert intent.question == "Which room do you mean?"


def test_close_the_garage_proposes_only_when_the_world_shows_it_open() -> None:
    open_intent = _classify("close the garage", world=_world(garage_open=True))
    assert isinstance(open_intent, ActionProposal)
    assert open_intent.action_kind is ActionKind.CLOSE_GARAGE
    assert open_intent.target_device_id == "garage_door"
    assert open_intent.target_selector is None
    assert open_intent.justification == "direct command: close the garage"

    door_phrase = _classify("close the garage door", world=_world(garage_open=True))
    assert isinstance(door_phrase, ActionProposal)
    assert door_phrase.action_kind is ActionKind.CLOSE_GARAGE

    closed = _classify("close the garage", world=_world(garage_open=False))
    assert isinstance(closed, ConversationMessage)


def test_close_the_garage_without_a_world_asks_for_clarification() -> None:
    intent = _classify("close the garage")

    assert isinstance(intent, ClarificationRequest)


def test_open_the_garage_is_a_direct_proposal() -> None:
    intent = _classify("open the garage", world=_world())

    assert isinstance(intent, ActionProposal)
    assert intent.action_kind is ActionKind.OPEN_GARAGE
    assert intent.target_device_id == "garage_door"


def test_turn_on_is_an_honest_capability_gap() -> None:
    for text in ("turn on the office light", "turn on the light"):
        intent = _classify(text, world=_world())
        assert isinstance(intent, ConversationMessage), text
        assert "turn lights on" in intent.text


def test_known_automation_phrasing_becomes_a_rule_draft() -> None:
    interpreter = DeterministicIntentInterpreter(
        draft_for=_fixture_drafter(), principal=RESIDENT, now=NOW
    )

    intent = interpreter.classify(EXAMPLE, world=None, room_focus=None)

    assert isinstance(intent, RuleDraft)
    assert intent.source_text == EXAMPLE
    assert intent.proposed_by == RESIDENT.actor_id


def test_unknown_automation_phrasing_is_honest_conversation() -> None:
    interpreter = DeterministicIntentInterpreter(
        draft_for=_fixture_drafter(), principal=RESIDENT, now=NOW
    )

    intent = interpreter.classify("when the sun sets, open the garage", world=None, room_focus=None)

    assert isinstance(intent, ConversationMessage)


def test_automation_phrasing_without_a_drafter_is_honest_conversation() -> None:
    intent = _classify("when i'm working late, dim the office light")

    assert isinstance(intent, ConversationMessage)


def test_unknown_and_empty_utterances_become_conversation() -> None:
    for text in ("play some jazz", "hello there"):
        intent = _classify(text)
        assert isinstance(intent, ConversationMessage), text
        assert intent.text == text

    with pytest.raises(ValueError):
        _classify("   ")


def test_garage_commands_take_priority_over_question_leaders() -> None:
    # "can ..." is a question leader, but a direct command shapes the intent.
    intent = _classify("close the garage?", world=_world())

    assert isinstance(intent, ActionProposal)
    assert intent.action_kind is ActionKind.CLOSE_GARAGE


# -- the provider contract ------------------------------------------------------


def _context(*, world: WorldView | None = None, room_focus: str | None = None) -> AgentContext:
    return AgentContext(
        household_id=RESIDENT.household_id,
        actor_id=RESIDENT.actor_id,
        actor_role=RESIDENT.role_tier.name,
        room_focus=room_focus,
        world=world,
    )


def test_fixture_provider_interpret_intent_returns_interpreter_results() -> None:
    provider = ScriptedIntelligenceProvider()

    question = provider.interpret_intent(
        "is the garage open?", context=_context(), principal=RESIDENT, now=NOW
    )
    assert isinstance(question, QueryRequest)

    draft = provider.interpret_intent(EXAMPLE, context=_context(), principal=RESIDENT, now=NOW)
    assert isinstance(draft, RuleDraft)
    assert draft.unresolved

    proposal = provider.interpret_intent(
        "close the garage",
        context=_context(world=_world(garage_open=True)),
        principal=RESIDENT,
        now=NOW,
    )
    assert isinstance(proposal, ActionProposal)
    assert proposal.action_kind is ActionKind.CLOSE_GARAGE

    clarify = provider.interpret_intent(
        "turn that light off", context=_context(), principal=RESIDENT, now=NOW
    )
    assert isinstance(clarify, ClarificationRequest)

    with pytest.raises(ValueError):
        provider.interpret_intent(
            "is the garage open?",
            context=_context(),
            principal=RESIDENT,
            now=NOW.replace(tzinfo=None),
        )


# -- the model bridge -----------------------------------------------------------


def _bridge(tmp: str, handle_factory) -> ModelIntelligenceProvider:
    backend = FakeBackend(handle_factory=handle_factory)
    manager = _manager_with_backend(tmp, backend)
    _install_local(
        manager,
        Path(tmp) / "models",
        "fake-structured",
        kind=ModelKind.INTELLIGENCE,
        capabilities={"chat", "structured_intent"},
    )
    manager.load("fake-structured")
    return ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)


def test_bridge_interpret_intent_falls_back_to_the_deterministic_floor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        provider = _bridge(tmp, lambda descriptor: ScriptedHandle(descriptor))

        intent = provider.interpret_intent(
            "close the garage",
            context=_context(world=_world(garage_open=True)),
            principal=RESIDENT,
            now=NOW,
        )

        assert isinstance(intent, ActionProposal)
        assert intent.action_kind is ActionKind.CLOSE_GARAGE
        assert intent.justification == "direct command: close the garage"


def test_bridge_interpret_intent_uses_a_valid_structured_action_payload() -> None:
    class StructuredIntentHandle(ScriptedHandle):
        def capability_method(self, name, requires, payload):
            assert name == "structured_intent"
            return {
                "form": "action",
                "action_kind": "close_garage",
                "target_device_id": "garage_door",
                "justification": "model-proposed direct close",
                "source_text": payload["text"],
            }

    with tempfile.TemporaryDirectory() as tmp:
        provider = _bridge(tmp, lambda descriptor: StructuredIntentHandle(descriptor))

        intent = provider.interpret_intent(
            "please close the garage",
            context=_context(world=_world(garage_open=True)),
            principal=RESIDENT,
            now=NOW,
        )

        assert isinstance(intent, ActionProposal)
        assert intent.action_kind is ActionKind.CLOSE_GARAGE
        assert intent.target_device_id == "garage_door"
        assert intent.justification == "model-proposed direct close"
        assert intent.source_text == "please close the garage"


def test_bridge_interpret_intent_falls_back_on_a_malformed_payload() -> None:
    class GapHandle(ScriptedHandle):
        def capability_method(self, name, requires, payload):
            return {"form": "action", "action_kind": "close_garage"}  # missing keys

    with tempfile.TemporaryDirectory() as tmp:
        provider = _bridge(tmp, lambda descriptor: GapHandle(descriptor))

        intent = provider.interpret_intent(
            "close the garage",
            context=_context(world=_world(garage_open=True)),
            principal=RESIDENT,
            now=NOW,
        )

        assert isinstance(intent, ActionProposal)
        assert intent.justification == "direct command: close the garage"
