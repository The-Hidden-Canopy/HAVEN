"""The intent union: every natural-language form validates in __post_init__.

An interpreter proposes exactly one of these; the union is the routing
vocabulary downstream (queries answer, actions go straight to authority,
rule drafts enter the lifecycle, clarifications go back to the human).
"""

import dataclasses
import typing
from datetime import time

import pytest

from haven.core.domain import ActionKind, DeviceSelector, IntentForm, RuleDraft, ScheduleTrigger
from haven.intelligence.intents import (
    ActionProposal,
    ClarificationRequest,
    ConversationMessage,
    Intent,
    MutationProposal,
    QueryRequest,
    RuleProposal,
)


def _rule_draft() -> RuleDraft:
    return RuleDraft(
        draft_id="draft-1",
        household_id="household-a",
        proposed_by="resident-1",
        source_text="turn off the bedroom lights at 11pm",
        interpretation="At 23:00 every day, turn off the bedroom lights.",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        schedule_trigger=ScheduleTrigger(time_of_day=time(23, 0)),
        target_device_id="bedroom_lights",
    )


def _proposal(**overrides) -> ActionProposal:
    values = {
        "action_kind": ActionKind.SET_LIGHT_BRIGHTNESS,
        "target_device_id": "bedroom_lights",
        "target_selector": None,
        "parameters": (("brightness_pct", 20),),
        "justification": "The member asked for this directly.",
        "source_text": "set the bedroom lights to 20%",
    }
    values.update(overrides)
    return ActionProposal(**values)


def test_query_request_carries_the_raw_text() -> None:
    query = QueryRequest(text="  is the garage still open?  ")
    assert query.text == "is the garage still open?"
    assert isinstance(query, IntentForm)


@pytest.mark.parametrize("text", ["", "   ", 42, None])
def test_query_request_rejects_empty_text(text) -> None:
    with pytest.raises(ValueError, match="query text"):
        QueryRequest(text=text)


def test_action_proposal_validates_and_normalizes() -> None:
    proposal = _proposal(parameters=(("brightness_pct", 20),))
    assert proposal.parameters == (("brightness_pct", 20),)
    assert isinstance(proposal, IntentForm)


def test_action_proposal_requires_exactly_one_target() -> None:
    selector = DeviceSelector(room="bedroom")
    with pytest.raises(ValueError, match="exactly one of target_device_id or target_selector"):
        _proposal(target_device_id=None, target_selector=None)
    with pytest.raises(ValueError, match="exactly one of target_device_id or target_selector"):
        _proposal(target_device_id="bedroom_lights", target_selector=selector)


def test_action_proposal_accepts_a_selector_target() -> None:
    proposal = _proposal(target_device_id=None, target_selector=DeviceSelector(room="bedroom"))
    assert proposal.target_selector == DeviceSelector(room="bedroom")


def test_action_proposal_rejects_a_non_selector_target() -> None:
    with pytest.raises(ValueError, match="target_selector must be a DeviceSelector"):
        _proposal(target_device_id=None, target_selector="bedroom")


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_kind": "set_light_brightness"},
        {"justification": " "},
        {"justification": ""},
        {"source_text": ""},
        {"parameters": (("brightness_pct", 20), ("brightness_pct", 30))},
        {"target_device_id": "  "},
    ],
)
def test_action_proposal_rejects_invalid_fields(overrides) -> None:
    with pytest.raises(ValueError):
        _proposal(**overrides)


def test_mutation_proposal_is_frozen_and_side_effect_free() -> None:
    proposal = MutationProposal(
        entity_kind="room",
        operation="create",
        attributes=(("name", "Office"),),
        source_text="Add an office.",
    )
    assert proposal.entity_kind == "room"
    assert proposal.attributes == (("name", "Office"),)
    assert isinstance(proposal, IntentForm)
    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.operation = "delete"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"entity_kind": "device"},
        {"operation": "execute"},
        {"entity_kind": "room", "operation": "create", "target_id": "office"},
        {"entity_kind": "room", "operation": "rename", "target_id": " "},
    ],
)
def test_mutation_proposal_rejects_invalid_fields(overrides) -> None:
    values = {
        "entity_kind": "room",
        "operation": "create",
        "attributes": (("name", "Office"),),
        "source_text": "Add an office.",
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        MutationProposal(**values)


def test_rule_proposal_is_the_rule_draft_itself() -> None:
    draft = _rule_draft()
    assert RuleProposal is RuleDraft
    assert isinstance(draft, IntentForm)


def test_clarification_request_validates() -> None:
    request = ClarificationRequest(
        question="Which bedroom did you mean?",
        source_text="turn off the bedroom lights",
        options=("master bedroom", "guest bedroom"),
    )
    assert request.options == ("master bedroom", "guest bedroom")
    assert isinstance(request, IntentForm)


def test_clarification_request_defaults_to_open_ended() -> None:
    request = ClarificationRequest(question="Which bedroom did you mean?", source_text="turn off the lights")
    assert request.options == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"question": ""},
        {"source_text": " "},
        {"options": ("",)},
        {"options": (42,)},
    ],
)
def test_clarification_request_rejects_invalid_fields(overrides) -> None:
    values = {"question": "Which bedroom?", "source_text": "turn off the lights"}
    values.update(overrides)
    with pytest.raises(ValueError):
        ClarificationRequest(**values)


def test_conversation_message_validates() -> None:
    message = ConversationMessage(text="good morning")
    assert isinstance(message, IntentForm)


@pytest.mark.parametrize("text", ["", "   "])
def test_conversation_message_rejects_empty_text(text) -> None:
    with pytest.raises(ValueError, match="message text"):
        ConversationMessage(text=text)


def test_intent_alias_covers_every_form() -> None:
    assert set(typing.get_args(Intent)) == {
        QueryRequest,
        ActionProposal,
        MutationProposal,
        RuleDraft,
        ClarificationRequest,
        ConversationMessage,
    }


def test_intent_forms_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        QueryRequest(text="hello").text = "changed"  # type: ignore[misc]
