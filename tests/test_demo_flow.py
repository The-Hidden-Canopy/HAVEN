"""Demo director flows without HTTP: scenario ask, approve/deny, chat intents,
fail-closed confirmation reuse, reset, voice session state machine, and the
automations/system slices of the state contract.
"""

import time
from datetime import datetime, timedelta, timezone

from haven.core.domain import (
    ActionStatus,
    ConfirmationToken,
    DecisionCode,
    DecisionStatus,
    Principal,
    RoleTier,
)
from haven.providers import ProviderCapabilities, build_default_registry
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
    assert state["glow_target"] == "garage"
    assert len(state["pending"]) == 1
    pending = state["pending"][0]
    assert pending["title"] == "Close the garage door?"
    assert "18 minutes" in pending["detail"]
    assert datetime.fromisoformat(pending["expires_at"]) == NOW + timedelta(minutes=5)
    texts = [entry["text"] for entry in state["conversation"]]
    assert "Garage has been open 18 minutes with no nearby motion." in texts
    assert "Want me to close it?" in texts
    assert state["status"]["line"] == "HAVEN needs your attention"
    assert _room(state, "driveway")["camera"]["online"] is True


def test_approve_closes_garage_and_executes_through_the_real_path() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]

    state = director.approve(request_id)

    assert state is not None
    assert state["glow"] == "completed"
    assert state["glow_target"] == "garage"
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
    assert state["glow_target"] is None
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
    assert state["glow_target"] == "office"
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


def _clear_the_garage_ask(director: DemoDirector) -> None:
    director.deny(director.state()["pending"][0]["request_id"])


def test_camera_down_glows_critical_and_fails_closed() -> None:
    director = _director()
    _clear_the_garage_ask(director)

    state = director.mark_camera_down()

    assert state["glow"] == "critical"
    assert state["glow_target"] == "driveway"
    assert state["status"]["line"] == "HAVEN can't see clearly"
    assert _room(state, "driveway")["camera"]["online"] is False
    texts = [entry["text"] for entry in state["conversation"]]
    assert "I can't confirm the driveway camera is up." in texts
    assert "The driveway camera's evidence is unavailable; I won't act on guesswork." in texts

    # The UNAVAILABLE decision came from the real engine path, and fail
    # closed means nothing reached the execution adapter.
    receipt = director.receipts[-1]
    assert receipt.decision.status == DecisionStatus.UNAVAILABLE
    assert receipt.decision.code == DecisionCode.EVIDENCE_UNAVAILABLE
    blocked = [action for action in director.store.state.actions if action.status == ActionStatus.BLOCKED]
    assert any(action.request.target_device_id == "driveway_cam" for action in blocked)
    assert [c for c in director.adapter.commands if c.service == "camera.record_clip"] == []


def test_camera_up_restores_the_rule_and_records_the_clip() -> None:
    director = _director()
    _clear_the_garage_ask(director)
    director.mark_camera_down()

    state = director.mark_camera_up()

    assert state["glow"] == "completed"
    assert state["glow_target"] == "driveway"
    assert state["status"]["line"] == "Everything nominal"
    assert _room(state, "driveway")["camera"]["online"] is True
    clips = [c for c in director.adapter.commands if c.service == "camera.record_clip"]
    assert len(clips) == 1
    assert clips[0].target_device_id == "driveway_cam"
    texts = [entry["text"] for entry in state["conversation"]]
    assert "The driveway camera is back online." in texts
    assert "Evidence restored — I recorded a driveway clip." in texts
    executed = [action for action in director.store.state.actions if action.status == ActionStatus.EXECUTED]
    assert any(action.request.target_device_id == "driveway_cam" for action in executed)


def test_reset_restores_the_camera_and_clears_critical() -> None:
    director = _director()
    _clear_the_garage_ask(director)
    director.mark_camera_down()
    assert director.state()["glow"] == "critical"

    state = director.reset()

    assert state["glow"] == "permission"
    assert state["glow_target"] == "garage"
    assert director.house.camera_down() is False
    assert state["status"]["line"] == "HAVEN needs your attention"


def test_activity_and_memory_surface_the_store() -> None:
    director = _director()
    request_id = director.state()["pending"][0]["request_id"]
    state = director.approve(request_id)

    event_types = {row["event_type"] for row in state["activity"]}
    assert "rule_proposed" in event_types
    assert "rule_approved" in event_types
    assert "action_authorized" in event_types
    assert "action_executed" in event_types
    first = state["activity"][0]
    assert set(first) == {"event_id", "event_type", "actor_id", "occurred_at", "summary"}
    assert "Rule approved: close the garage by gerron" in {row["summary"] for row in state["activity"]}

    assert len(state["memory"]) == 2
    entry = state["memory"][0]
    assert set(entry) == {"entry_id", "kind", "content", "recorded_at"}
    assert all(row["kind"] == "approved_rule" for row in state["memory"])
    assert {row["content"] for row in state["memory"]} == {
        "When the garage door has been open a while with no nearby motion, ask to close it.",
        "Record a short clip on the driveway camera when asked; the rule "
        "only runs while the camera's own evidence is current.",
    }


def _automation_rows(state: dict) -> dict[str, dict]:
    rows = state["automations"]
    assert all(
        set(row) == {"rule_id", "summary", "action", "target", "status", "approved_at"} for row in rows
    )
    return {row["rule_id"]: row for row in rows}


def test_automations_list_the_preapproved_rules_with_targets_and_status() -> None:
    director = _director()
    rows = _automation_rows(director.state())

    garage = rows[director.garage_rule_id]
    assert garage["summary"] == "close the garage"
    assert garage["action"] == "close"
    assert garage["target"] == "garage_door · Garage"
    assert garage["status"] == "approved"
    assert garage["approved_at"] == NOW.isoformat()

    camera = rows[director.camera_rule_id]
    assert camera["summary"] == "record a driveway clip"
    assert camera["action"] == "record_clip"
    assert camera["target"] == "driveway_cam · Driveway"
    assert camera["status"] == "approved"


def test_automations_gain_chat_created_light_rules() -> None:
    director = _director()

    state = director.chat("turn that light off", "office")

    rows = _automation_rows(state)
    assert len(rows) == 3
    light = next(row for row in rows.values() if row["summary"] == "turn that light off")
    assert light["action"] == "power"
    assert light["target"] == "office · light"
    assert light["status"] == "approved"
    assert light["rule_id"] != director.garage_rule_id


def test_system_reports_revision_counts_engine_and_providers() -> None:
    director = _director()
    system = director.state()["system"]

    assert system["revision"] == director.store.state.revision
    assert system["event_count"] == len(director.store.events)
    assert system["memory_count"] == len(director.store.state.memory)
    engine = director.engine
    assert system["engine"]["human_override_minutes"] == engine.human_override_window.total_seconds() / 60
    assert system["engine"]["minimum_confidence"] == engine.minimum_confidence

    providers = {row["provider_id"]: row for row in system["providers"]}
    assert len(providers) == 5
    assert providers["haven.fixture_intelligence"] == {
        "kind": "intelligence",
        "provider_id": "haven.fixture_intelligence",
        "capabilities": ["chat", "explain", "interpret", "propose_rule"],
    }
    assert providers["haven.fixture_wake_word"]["kind"] == "wake_word"
    assert providers["haven.fixture_vad"]["kind"] == "vad"
    assert providers["haven.fixture_speech_to_text"]["kind"] == "speech_to_text"
    assert providers["haven.fixture_text_to_speech"]["kind"] == "text_to_speech"


def test_system_providers_reflect_extra_registrations() -> None:
    registry = build_default_registry()
    registry.register(
        object(),
        capabilities=ProviderCapabilities(
            provider_id="extra.speech",
            kind="text_to_speech",
            capabilities=("streaming",),
        ),
    )
    director = DemoDirector(clock=lambda: NOW, capability_registry=registry)

    provider_ids = {row["provider_id"] for row in director.state()["system"]["providers"]}
    assert "extra.speech" in provider_ids


def test_system_revision_increases_after_a_mutation() -> None:
    director = _director()
    before = director.state()["system"]["revision"]

    director.chat("turn that light off", "office")

    after = director.state()["system"]
    assert after["revision"] > before
    assert after["revision"] == director.store.state.revision


def _listening(director: DemoDirector) -> None:
    assert director.voice.wake() is True
    director.voice.expire_ack()
    assert director.voice.state == "listening"


def test_voice_defaults_to_dormant_with_mic_off() -> None:
    director = _director()

    state = director.state()

    assert state["voice"] == {"state": "dormant", "mic": False}


def test_voice_transitions_publish_state_events() -> None:
    director = _director()
    subscriber = director.subscribe()

    assert director.voice.wake() is True
    kind, payload = subscriber.get(timeout=1)
    assert kind == "state"
    assert payload["voice"] == {"state": "wake", "mic": False}

    director.voice.expire_ack()
    kind, payload = subscriber.get(timeout=1)
    assert kind == "state"
    assert payload["voice"] == {"state": "listening", "mic": True}

    director.voice_cancel()
    kind, payload = subscriber.get(timeout=1)
    assert kind == "state"
    assert payload["voice"] == {"state": "dormant", "mic": False}


def test_voice_wake_advances_to_listening_on_the_ack_timer() -> None:
    director = DemoDirector(voice_ack_seconds=0.1)

    assert director.voice.wake() is True
    assert director.voice.state == "wake"

    deadline = time.monotonic() + 5
    while director.voice.state != "listening" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert director.voice.state == "listening"
    assert director.state()["voice"] == {"state": "listening", "mic": True}


def test_voice_utterance_close_the_garage_matches_typed_chat() -> None:
    director = _director()
    director.deny(director.state()["pending"][0]["request_id"])
    _listening(director)

    result = director.voice_utterance("close the garage")

    assert result["ok"] is True
    state = result["state"]
    assert state["voice"] == {"state": "dormant", "mic": False}
    assert len(state["pending"]) == 1
    assert state["pending"][0]["rule_id"] == director.garage_rule_id
    assert state["glow"] == "permission"
    assert {"from": "user", "text": "close the garage"} in state["conversation"]
    assert director.house.garage_open() is True


def test_voice_utterance_turns_off_the_focused_room_light() -> None:
    director = _director()
    _listening(director)

    result = director.voice_utterance("turn that light off")

    assert result["ok"] is True
    state = result["state"]
    assert _device(state, "office", "office_light")["is_on"] is False
    assert state["conversation"][-1] == {"from": "haven", "text": "Done — the office light is off."}
    assert {"from": "user", "text": "turn that light off"} in state["conversation"]
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1
    assert light_commands[0].target_device_id == "office_light"


def test_voice_wake_is_refused_during_the_refractory_cooldown() -> None:
    director = _director()
    _listening(director)
    assert director.voice_utterance("turn that light off")["ok"] is True

    result = director.voice_wake()

    assert result["ok"] is False
    assert result["state"]["voice"] == {"state": "dormant", "mic": False}

    director.voice.expire_refractory()
    assert director.voice_wake()["ok"] is True


def test_voice_utterance_is_refused_unless_listening() -> None:
    director = _director()

    assert director.voice_utterance("close the garage")["ok"] is False
    assert director.voice.state == "dormant"

    assert director.voice.wake() is True
    assert director.voice_utterance("close the garage")["ok"] is False
    assert director.voice.state == "wake"
    director.voice.expire_ack()
    assert director.voice.cancel() is True


def test_voice_cancel_while_listening_stops_without_acting() -> None:
    director = _director()
    director.deny(director.state()["pending"][0]["request_id"])
    _listening(director)

    result = director.voice_cancel()

    assert result["ok"] is True
    state = result["state"]
    assert state["voice"] == {"state": "dormant", "mic": False}
    assert state["conversation"][-1] == {"from": "haven", "text": "Stopped."}
    assert director.house.garage_open() is True
    assert [c for c in director.adapter.commands] == []
    assert director.voice_wake()["ok"] is False


def test_voice_stop_denies_a_pending_permission_request() -> None:
    director = _director()
    assert director.state()["pending"]
    _listening(director)

    result = director.voice_utterance("stop")

    assert result["ok"] is True
    state = result["state"]
    assert state["pending"] == []
    assert state["glow"] == "idle"
    texts = [entry["text"] for entry in state["conversation"]]
    assert "Understood — I'll leave the garage as it is." in texts
    assert director.house.garage_open() is True
    assert [c for c in director.adapter.commands] == []


def test_voice_cancel_while_acting_replies_honestly_and_does_not_recall() -> None:
    director = _director()
    director.deny(director.state()["pending"][0]["request_id"])
    _listening(director)
    assert director.voice_utterance("turn that light off")["ok"] is True
    assert len([c for c in director.adapter.commands if c.service == "light.turn_off"]) == 1
    director.voice.expire_refractory()
    _listening(director)
    director._set_glow("acting", target="office")

    result = director.voice_cancel()

    assert result["ok"] is True
    state = result["state"]
    assert state["voice"] == {"state": "dormant", "mic": False}
    assert state["conversation"][-1] == {"from": "haven", "text": "That action is already executing."}
    # The executed command stands; nothing was recalled or re-run.
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1
    assert _device(state, "office", "office_light")["is_on"] is False


def test_voice_cancel_while_dormant_is_a_noop() -> None:
    director = _director()

    result = director.voice_cancel()

    assert result["ok"] is False
    assert result["state"]["voice"] == {"state": "dormant", "mic": False}


def test_reset_clears_an_engaged_voice_session() -> None:
    director = _director()
    _listening(director)
    director.voice_cancel()
    assert director.voice_wake()["ok"] is False

    state = director.reset()

    assert state["voice"] == {"state": "dormant", "mic": False}
    # Refractory is cleared too: wake works immediately after a reset.
    assert director.voice.wake() is True
    director.voice.reset()
