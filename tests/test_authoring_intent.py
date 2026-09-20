from haven.intelligence.intents import MutationProposal
from haven.web.authoring_intent import parse_authoring_intent


def test_parser_proposes_room_creation_without_mutating_anything() -> None:
    proposal = parse_authoring_intent("Add an office.")

    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "room"
    assert proposal.operation == "create"
    assert proposal.target_id is None
    assert dict(proposal.attributes) == {"name": "office"}


def test_parser_proposes_focused_room_rename() -> None:
    proposal = parse_authoring_intent("Call this room the shop.", focus="office")

    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "room"
    assert proposal.operation == "rename"
    assert proposal.target_id == "office"
    assert dict(proposal.attributes) == {"name": "shop"}


def test_parser_proposes_room_delete_by_display_name() -> None:
    proposal = parse_authoring_intent("Remove the old guest room.")

    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "room"
    assert proposal.operation == "delete"
    assert proposal.target_id is None
    assert dict(proposal.attributes) == {"name": "old guest room"}


def test_parser_proposes_person_declaration() -> None:
    proposal = parse_authoring_intent("Bryan lives here too.")

    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "person"
    assert proposal.operation == "create"
    assert dict(proposal.attributes) == {"name": "Bryan", "role": "member"}


def test_parser_proposes_an_explicit_context_declaration() -> None:
    proposal = parse_authoring_intent(
        "Add a context called Working late using input_boolean.working_late."
    )

    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "context"
    assert proposal.operation == "create"
    assert dict(proposal.attributes) == {
        "label": "Working late",
        "entity_id": "input_boolean.working_late",
    }


def test_parser_proposes_a_weekday_light_automation_without_approving_it() -> None:
    proposal = parse_authoring_intent("Every weekday at 7 turn off the office light.")

    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "automation"
    assert proposal.operation == "create"
    assert dict(proposal.attributes) == {
        "action": "light.turn_off",
        "room": "office",
        "time_of_day": "07:00",
        "weekdays": (0, 1, 2, 3, 4),
    }


def test_parser_requires_an_explicit_automation_focus_for_schedule_edit() -> None:
    assert parse_authoring_intent("Don't run that automation on Fridays.") is None
    proposal = parse_authoring_intent(
        "Don't run that automation on Fridays.", automation_focus="rule-1"
    )
    assert isinstance(proposal, MutationProposal)
    assert proposal.entity_kind == "automation"
    assert proposal.operation == "update"
    assert proposal.target_id == "rule-1"
    assert dict(proposal.attributes) == {"remove_weekday": 4}


def test_parser_declines_ambiguous_or_unsupported_phrases() -> None:
    assert parse_authoring_intent("Call this room the shop.") is None
    assert parse_authoring_intent("Remove the light.") is None
    assert parse_authoring_intent("Every weekday at 7 turn off the office fan.") is None
    assert parse_authoring_intent("Every weekday at 25 turn off the office light.") is None
    assert parse_authoring_intent("Add a context called Working late.") is None
