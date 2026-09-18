"""Demo director flows without HTTP: scenario ask, approve/deny, chat intents,
fail-closed confirmation reuse, reset, voice session state machine, and the
automations/system slices of the state contract.
"""

import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from haven.core.domain import (
    ActionStatus,
    ConfirmationToken,
    DecisionCode,
    DecisionStatus,
    Principal,
    RoleTier,
)
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.models import BackendRegistry, ModelKind, ModelManager
from haven.models.manifest import ModelManifest, manifest_filename
from haven.providers import ProviderCapabilities, build_default_registry
from haven.web.demo import PROVIDER_ID, DemoDirector

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


def test_chat_close_the_garage_asks_confirmation_as_a_direct_action() -> None:
    director = _director()
    first_request = director.state()["pending"][0]["request_id"]
    director.deny(first_request)

    state = director.chat("close the garage")

    assert len(state["pending"]) == 1
    # A direct action is not tied to the stored garage rule; the card's
    # rule_id is the action's own request id as a correlation key, and the
    # request's origin -- never an id string -- marks it as direct.
    assert state["pending"][0]["rule_id"] != director.garage_rule_id
    assert state["pending"][0]["rule_id"] == state["pending"][0]["request_id"]
    assert state["pending"][0]["title"] == "Close the garage door?"
    assert state["glow"] == "permission"
    assert director.house.garage_open() is True
    # Direct actions never touch the rule store.
    assert len(director.store.state.rules) == 3


def test_chat_unknown_intent_gets_a_plain_refusal() -> None:
    director = _director()

    state = director.chat("play some jazz")

    assert state["conversation"][-1] == {"from": "haven", "text": "I can't do that yet."}
    assert [c for c in director.adapter.commands] == []


# -- queries: deterministic world answers without a model ---------------------


def test_chat_garage_question_without_model_answers_from_the_world() -> None:
    director = _director()

    state = director.chat("is the garage open?")

    assert state["conversation"][-1] == {"from": "haven", "text": "The garage door is open."}
    assert [c for c in director.adapter.commands] == []


def test_chat_room_light_question_without_model_answers_from_the_world() -> None:
    director = _director()

    state = director.chat("is the office light on?")

    assert state["conversation"][-1] == {"from": "haven", "text": "The office light is on."}


def test_chat_who_is_home_answers_from_presence() -> None:
    director = _director()

    state = director.chat("who is home?")

    assert state["conversation"][-1] == {"from": "haven", "text": "Gerron is home."}


def test_chat_unanswerable_question_without_model_is_honest() -> None:
    director = _director()

    state = director.chat("why is the sky blue?")

    assert state["conversation"][-1] == {
        "from": "haven",
        "text": "I can't answer that without an intelligence model.",
    }
    assert [c for c in director.adapter.commands] == []


# -- queries: a loaded, assigned chat model answers instead -------------------


class _ScriptedChatHandle:
    """A loaded model handle with a canned reply, recording its messages."""

    def __init__(self, descriptor, reply: str) -> None:
        self._descriptor = descriptor
        self.reply = reply
        self.messages: list = []

    @property
    def descriptor(self):
        return self._descriptor

    def chat(self, messages, **params):
        self.messages.append(list(messages))
        return {"text": self.reply}

    def unload(self):
        pass


class _StaticBackend:
    def __init__(self, handle) -> None:
        self._handle = handle

    def load(self, descriptor, model_dir):
        return self._handle


def _director_with_chat_model(tmp: str, reply: str):
    handle = _ScriptedChatHandle(None, reply)
    registry = BackendRegistry()
    registry.register("fake", _StaticBackend(handle))
    manager = ModelManager(Path(tmp) / "root", backends=registry)
    manifest = ModelManifest(
        id="fake-chat",
        version="1.0.0",
        kind=ModelKind.INTELLIGENCE,
        capabilities=frozenset({"chat"}),
        architecture="fake-arch",
        backend="fake",
        files={"weights": "weights.bin"},
    )
    folder = Path(tmp) / "models" / "fake-chat"
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"stub weights")
    manifest.save(folder / manifest_filename())
    manager.install_local_folder(folder)
    manager.load("fake-chat")
    manager.assign("chat", "fake-chat")
    director = DemoDirector(clock=lambda: NOW, model_manager=manager)
    return director, handle


def test_chat_garage_question_with_loaded_chat_model_speaks_the_models_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        director, handle = _director_with_chat_model(tmp, "the garage door is open, per the world")

        state = director.chat("is the garage still open?")

        assert state["conversation"][-1] == {
            "from": "haven",
            "text": "the garage door is open, per the world",
        }
        system = handle.messages[0][0]
        assert system["role"] == "system"
        # The AgentContext carried the bounded world with the garage device.
        assert "garage_door" in system["content"]


# -- direct actions: clarification and the untouched rule store ---------------


def test_chat_light_off_with_two_lights_in_focus_asks_which_one() -> None:
    director = _director()
    director.registry.register(
        DeviceManifest(
            device_id="office_lamp",
            device_type="light",
            provider_id=PROVIDER_ID,
            room="office",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
            ),
        )
    )

    state = director.chat("turn that light off", "office")

    assert state["conversation"][-1] == {
        "from": "haven",
        "text": "Which one? There are 2 lights in the office.",
    }
    assert [c for c in director.adapter.commands if c.service == "light.turn_off"] == []
    assert len(director.store.state.rules) == 3


def test_chat_named_room_light_off_runs_directly_without_a_rule() -> None:
    director = _director()

    state = director.chat("turn off the office light")

    assert state["glow"] == "completed"
    assert _device(state, "office", "office_light")["is_on"] is False
    assert state["conversation"][-1] == {"from": "haven", "text": "Done — the office light is off."}
    assert len(director.store.state.rules) == 3  # no rule created for a direct action
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1
    assert light_commands[0].target_device_id == "office_light"


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

    assert len(state["memory"]) == 3
    entry = state["memory"][0]
    assert set(entry) == {"entry_id", "kind", "content", "recorded_at"}
    assert all(row["kind"] == "approved_rule" for row in state["memory"])
    assert {row["content"] for row in state["memory"]} == {
        "When the garage door has been open a while with no nearby motion, ask to close it.",
        "Record a short clip on the driveway camera when asked; the rule "
        "only runs while the camera's own evidence is current.",
        "Turn off the office light at 22:35 every night.",
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


def test_chat_light_off_runs_as_a_direct_action_without_creating_a_rule() -> None:
    director = _director()

    state = director.chat("turn that light off", "office")

    rows = _automation_rows(state)
    # The direct action bypasses the propose/approve lifecycle entirely.
    assert len(rows) == 3
    assert len(director.store.state.rules) == 3
    assert _device(state, "office", "office_light")["is_on"] is False
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1


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
    assert state["pending"][0]["rule_id"] != director.garage_rule_id
    assert state["pending"][0]["rule_id"] == state["pending"][0]["request_id"]
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


# -- the scheduler: quiet architectural operation, another requester -------------


def _clock_box(start=NOW):
    box = {"now": start}
    return box, lambda: box["now"]


def _due() -> datetime:
    return NOW.replace(hour=22, minute=35)


def test_run_scheduler_tick_executes_the_office_light_schedule() -> None:
    box, clock = _clock_box()
    director = DemoDirector(clock=clock)
    box["now"] = _due()

    receipts = director.run_scheduler_tick()

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.outcome == "executed"
    assert receipt.requested_action.rule_id == director.office_light_rule_id
    assert receipt.requested_action.justification == f"schedule due at {_due().isoformat()}"
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1
    assert light_commands[0].target_device_id == "office_light"

    state = director.state()
    assert _device(state, "office", "office_light")["is_on"] is False
    assert state["glow"] == "completed"
    assert state["glow_target"] == "office"
    # Successes stay silent: only the two scenario lines are present.
    texts = [entry["text"] for entry in state["conversation"]]
    assert "Garage has been open 18 minutes with no nearby motion." in texts
    assert "Want me to close it?" in texts
    assert len(texts) == 2
    # The run shows up in the operational layer and the activity feed.
    rows = {row["rule_id"]: row for row in state["scheduler"]}
    office = rows[director.office_light_rule_id]
    assert office["last_outcome"] == "executed"
    assert office["last_fired_at"] == _due().isoformat()
    assert office["due_now"] is False
    executed = [row for row in state["activity"] if row["event_type"] == "action_executed"]
    assert len(executed) == 1


def test_scheduler_blocked_run_adds_a_quiet_conversation_line() -> None:
    director = _director()
    director.scheduler.set_enabled(director.camera_rule_id, True)
    director.house.set_camera_down(True)

    receipts = director.run_scheduler_tick(now=NOW)

    assert len(receipts) == 1
    assert receipts[0].decision.code == DecisionCode.EVIDENCE_UNAVAILABLE
    state = director.state()
    assert state["glow"] == "critical"
    assert state["glow_target"] == "driveway"
    texts = [entry["text"] for entry in state["conversation"]]
    assert (
        "I was due to run record a driveway clip but evidence_unavailable: "
        in texts[-1]
    )
    rows = {row["rule_id"]: row for row in state["scheduler"]}
    assert rows[director.camera_rule_id]["last_outcome"] == "unavailable"


def test_scheduler_does_not_refire_within_the_window() -> None:
    box, clock = _clock_box()
    director = DemoDirector(clock=clock)
    box["now"] = _due()

    assert len(director.run_scheduler_tick()) == 1
    box["now"] = _due() + timedelta(minutes=2)
    assert director.run_scheduler_tick() == []
    light_commands = [c for c in director.adapter.commands if c.service == "light.turn_off"]
    assert len(light_commands) == 1


def test_state_includes_scheduler_status_rows() -> None:
    director = _director()

    rows = {row["rule_id"]: row for row in director.state()["scheduler"]}

    assert set(rows) == {
        director.garage_rule_id,
        director.camera_rule_id,
        director.office_light_rule_id,
    }
    for row in rows.values():
        assert set(row) == {
            "rule_id",
            "summary",
            "enabled",
            "due_now",
            "next_run_at",
            "last_fired_at",
            "last_outcome",
        }
    office = rows[director.office_light_rule_id]
    assert office["summary"] == "turn off the office light at 22:35 every day"
    assert office["enabled"] is True
    assert office["due_now"] is False
    assert office["next_run_at"] == "2026-09-16T22:35:00+00:00"
    assert office["last_fired_at"] is None
    assert office["last_outcome"] is None
    # The manual-flow rules carry 24h schedule windows as an "ask whenever
    # invoked" affordance; they stay out of the scheduler.
    assert rows[director.garage_rule_id]["enabled"] is False
    assert rows[director.camera_rule_id]["enabled"] is False


def test_scheduler_enabled_toggle_shows_up_in_state() -> None:
    director = _director()

    director.set_scheduler_enabled(director.office_light_rule_id, False)

    rows = {row["rule_id"]: row for row in director.state()["scheduler"]}
    assert rows[director.office_light_rule_id]["enabled"] is False
    # A disabled schedule does not fire even at its due instant.
    receipts = director.run_scheduler_tick(now=_due())
    assert receipts == []
    assert _device(director.state(), "office", "office_light")["is_on"] is True


def test_scheduler_thread_ticks_and_stops_cleanly() -> None:
    director = DemoDirector(clock=lambda: NOW, scheduler_tick_seconds=0.05)
    ticks: list = []
    original = director.run_scheduler_tick

    def spy(now=None):
        ticks.append(now)
        return original(now=now)

    director.run_scheduler_tick = spy
    director.start_scheduler()
    director.start_scheduler()  # idempotent
    deadline = time.monotonic() + 5
    while not ticks and time.monotonic() < deadline:
        time.sleep(0.01)
    thread = director._scheduler_thread
    director.stop_scheduler()

    assert ticks
    assert director._scheduler_thread is None
    assert thread is not None and not thread.is_alive()
    # Stopping twice, or never starting, is a no-op.
    director.stop_scheduler()
    fresh = _director()
    fresh.stop_scheduler()


def test_reset_stops_and_restarts_the_scheduler_thread() -> None:
    director = DemoDirector(clock=lambda: NOW, scheduler_tick_seconds=0.05)
    director.start_scheduler()
    first = director._scheduler_thread
    assert first is not None

    director.reset()

    assert director._scheduler_thread is not None
    assert director._scheduler_thread is not first
    assert not first.is_alive()
    director.stop_scheduler()
    assert director._scheduler_thread is None
