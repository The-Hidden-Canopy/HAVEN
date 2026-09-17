"""Demo director flows without HTTP: scenario ask, approve/deny, chat intents,
fail-closed confirmation reuse, and reset.
"""

from datetime import datetime, timedelta, timezone

from haven.core.domain import ActionStatus, ConfirmationToken, DecisionCode, Principal, RoleTier
from haven.web.demo import DemoDirector

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _director() -> DemoDirector:
    return DemoDirector(clock=lambda: NOW)


def _room(state: dict, room_id: str) -> dict:
    return next(room for room in state["rooms"] if room["id"] == room_id)


def _device(state: dict, room_id: str, device_id: str) -> dict:
    return next(device for device in _room(state, room_id)["devices"] if device["id"] == device_id)


def test_scenario_starts_by_asking_permission() -> None:
    director = _director()
    state = director.state()

    assert state["glow"] == "permission"
    assert len(state["pending"]) == 1
    pending = state["pending"][0]
    assert pending["title"] == "Close the garage door?"
    assert "18 minutes" in pending["detail"]
    assert datetime.fromisoformat(pending["expires_at"]) == NOW + timedelta(minutes=5)
    texts = [entry["text"] for entry in state["conversation"]]
    assert "Garage has been open 18 minutes with no nearby motion." in texts
    assert "Want me to close it?" in texts
    assert state["status"]["line"] == "HAVEN needs your attention"


def test_approve_closes_garage_and_executes_through_the_real_path() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]

    state = director.approve(request_id)

    assert state is not None
    assert state["glow"] == "completed"
    assert state["pending"] == []
    assert _device(state, "garage", "garage_door")["is_on"] is False
    assert director.house.garage_open() is False

    receipt = director.receipts[-1]
    assert receipt.outcome == "executed"
    executed = [action for action in director.store.state.actions if action.status == ActionStatus.EXECUTED]
    assert len(executed) == 1
    assert executed[0].request.target_device_id == "garage_door"
    assert executed[0].request.confirmation_token is not None

    close_commands = [c for c in director.adapter.commands if c.service == "cover.close"]
    assert len(close_commands) == 1
    assert director.state()["status"]["line"] == "Everything nominal"


def test_second_approve_of_the_same_request_is_unknown_and_runs_nothing() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]
    director.approve(request_id)

    assert director.approve(request_id) is None

    executed = [action for action in director.store.state.actions if action.status == ActionStatus.EXECUTED]
    assert len(executed) == 1


def test_reused_confirmation_token_fails_closed() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]
    token = ConfirmationToken(
        token_id="confirm-fixed",
        household_id=director.store.household_id,
        rule_id=director.garage_rule_id,
        request_id=request_id,
        confirmed_by=director.resident.actor_id,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    principal = Principal(
        actor_id="gerron", household_id=director.store.household_id, role_tier=RoleTier.MEMBER
    )

    first = director.runtime.run_rule(
        director.garage_rule_id,
        principal=principal,
        world=director.house.snapshot(NOW),
        justification="First approval.",
        now=NOW,
        confirmation_token=token,
    )
    second = director.runtime.run_rule(
        director.garage_rule_id,
        principal=principal,
        world=director.house.snapshot(NOW),
        justification="Reused approval.",
        now=NOW,
        confirmation_token=token,
    )

    assert first.outcome == "executed"
    assert second.decision.code == DecisionCode.CONFIRMATION_REUSED
    assert second.outcome == "deny"
    close_commands = [c for c in director.adapter.commands if c.service == "cover.close"]
    assert len(close_commands) == 1


def test_deny_dismisses_the_ask_without_acting() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]

    assert director.deny(request_id) is True

    state = director.state()
    assert state["glow"] == "idle"
    assert state["pending"] == []
    assert state["status"]["line"] == "Everything nominal"
    assert director.house.garage_open() is True
    executed = [action for action in director.store.state.actions if action.status == ActionStatus.EXECUTED]
    assert executed == []
    assert director.deny(request_id) is False


def test_chat_turns_off_the_focused_room_light_automatically() -> None:
    director = _director()

    state = director.chat("turn that light off", "office")

    assert state["glow"] == "completed"
    assert _device(state, "office", "office_light")["is_on"] is False
    assert state["conversation"][-1] == {"from": "haven", "text": "Done — the office light is off."}
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1
    assert light_commands[0].target_device_id == "office_light"


def test_chat_light_off_without_focus_asks_for_clarification() -> None:
    director = _director()
    rules_before = len(director.store.state.rules)

    state = director.chat("turn that light off")

    assert state["conversation"][-1] == {"from": "haven", "text": "Which room do you mean?"}
    assert _device(state, "office", "office_light")["is_on"] is True
    assert len(director.store.state.rules) == rules_before
    assert [c for c in director.adapter.commands if c.service == "light.turn_off"] == []


def test_chat_close_the_garage_reruns_the_confirm_flow() -> None:
    director = _director()
    first_request = director.state()["pending"][0]["request_id"]
    director.deny(first_request)

    state = director.chat("close the garage")

    assert len(state["pending"]) == 1
    assert state["pending"][0]["rule_id"] == director.garage_rule_id
    assert state["glow"] == "permission"
    assert director.house.garage_open() is True


def test_chat_unknown_intent_gets_a_plain_refusal() -> None:
    director = _director()

    state = director.chat("play some jazz")

    assert state["conversation"][-1] == {"from": "haven", "text": "I can't do that yet."}
    assert [c for c in director.adapter.commands] == []


def test_reset_restores_the_scenario() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]
    director.approve(request_id)
    assert director.house.garage_open() is False

    state = director.reset()

    assert director.house.garage_open() is True
    assert state["glow"] == "permission"
    assert len(state["pending"]) == 1
    assert state["conversation"] == [
        {"from": "haven", "text": "Garage has been open 18 minutes with no nearby motion."},
        {"from": "haven", "text": "Want me to close it?"},
    ]


def test_auto_allow_approves_future_requests_for_the_same_rule() -> None:
    director = _director()
    first_request = director.state()["pending"][0]["request_id"]
    director.approve(first_request, auto=True)
    assert director.house.garage_open() is False

    director.house.reset(now=NOW)
    director.start_scenario()

    state = director.state()
    assert state["pending"] == []
    assert state["glow"] == "completed"
    assert director.house.garage_open() is False
