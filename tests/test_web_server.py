"""HTTP contract for the web surface: JSON API over a live threaded server,
plus the SSE stream.
"""

import http.client
import json
import threading
from datetime import datetime, timezone

import pytest

from haven.web.server import make_server

UTC = timezone.utc
FIXED_NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


@pytest.fixture()
def server():
    instance, director = make_server(0, clock=lambda: FIXED_NOW)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, director, instance.server_address[1]
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=5)


def _get(port: int, path: str) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    status = response.status
    connection.close()
    return status, body


def _get_json(port: int, path: str) -> tuple[int, dict]:
    status, body = _get(port, path)
    return status, json.loads(body)


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else ""
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def _read_event(response: http.client.HTTPResponse) -> tuple[str | None, str]:
    event = None
    data: list[str] = []
    while True:
        line = response.readline()
        if not line:
            raise ConnectionError("SSE stream closed")
        line = line.decode("utf-8").rstrip("\n").rstrip("\r")
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            data.append(line[len("data: "):])
        elif line == "":
            if event is not None:
                return event, "\n".join(data)


def _room(state: dict, room_id: str) -> dict:
    return next(room for room in state["rooms"] if room["id"] == room_id)


def _device(state: dict, room_id: str, device_id: str) -> dict:
    return next(device for device in _room(state, room_id)["devices"] if device["id"] == device_id)


def test_get_state_matches_the_wire_contract(server) -> None:
    _, _, port = server
    status, state = _get_json(port, "/api/state")

    assert status == 200
    assert state["glow"] == "permission"
    assert state["glow_target"] == "garage"
    assert isinstance(state["revision"], int)
    for key in ("rooms", "contexts", "people", "pending", "conversation", "activity", "memory"):
        assert isinstance(state[key], list), key
    assert isinstance(state["status"], dict)
    assert {row["event_type"] for row in state["activity"]} >= {"rule_proposed", "rule_approved", "action_blocked"}
    assert state["memory"][0]["kind"] == "approved_rule"

    office = _room(state, "office")
    assert office["name"] == "Office"
    assert office["people"] == ["Gerron"]
    light = _device(state, "office", "office_light")
    assert light["is_on"] is True
    assert light["brightness_pct"] == 70
    assert datetime.fromisoformat(light["observed_at"]).tzinfo is not None

    driveway = _room(state, "driveway")
    assert driveway["camera"] == {"id": "driveway_cam", "label": "Driveway", "motion": False, "online": True}
    assert _room(state, "garage")["camera"] is None

    assert {context["id"] for context in state["contexts"]} == {"working_late", "vacation_mode"}
    assert state["people"] == [{"id": "gerron", "name": "Gerron", "room": "office"}]
    assert len(state["pending"]) == 1
    assert state["status"]["core"] == "Local Core"
    assert state["status"]["devices"] == 5
    assert state["status"]["line"] == "HAVEN needs your attention"


def test_get_state_includes_automations_and_system(server) -> None:
    _, director, port = server
    _, state = _get_json(port, "/api/state")

    rows = {row["rule_id"]: row for row in state["automations"]}
    assert rows[director.garage_rule_id]["action"] == "close"
    assert rows[director.garage_rule_id]["target"] == "garage_door · Garage"
    assert rows[director.garage_rule_id]["status"] == "approved"
    assert rows[director.camera_rule_id]["target"] == "driveway_cam · Driveway"

    system = state["system"]
    assert system["revision"] == state["revision"]
    assert system["event_count"] == len(director.store.events)
    assert system["memory_count"] == len(director.store.state.memory)
    assert system["engine"]["human_override_minutes"] == 90
    assert system["engine"]["minimum_confidence"] == 1.0
    provider_ids = {row["provider_id"] for row in system["providers"]}
    assert provider_ids == {
        "haven.fixture_intelligence",
        "haven.fixture_wake_word",
        "haven.fixture_vad",
        "haven.fixture_speech_to_text",
        "haven.fixture_text_to_speech",
    }


def test_mutating_post_bumps_system_revision_on_the_next_get(server) -> None:
    _, _, port = server
    _, before = _get_json(port, "/api/state")

    status, _ = _post(port, "/api/chat", {"text": "turn that light off", "focus": "office"})
    assert status == 200

    _, after = _get_json(port, "/api/state")
    assert after["system"]["revision"] > before["system"]["revision"]


def test_static_root_is_served_or_404s_gracefully(server) -> None:
    import tempfile

    _, _, port = server
    with tempfile.TemporaryDirectory() as static_dir:
        from pathlib import Path

        Path(static_dir, "styles.css").write_text("body { color: black; }", encoding="utf-8")
        instance, _, _ = server
        instance.static_root = Path(static_dir)

        status, body = _get(port, "/styles.css")
        assert status == 200
        assert "color: black" in body
        status, _ = _get(port, "/")
        assert status in (200, 404)
        status, _ = _get(port, "/../secret")
        assert status == 404


def test_approve_over_http_executes_and_updates_state(server) -> None:
    _, director, port = server
    _, initial = _get_json(port, "/api/state")
    request_id = initial["pending"][0]["request_id"]

    status, body = _post(port, f"/api/requests/{request_id}/approve", {})

    assert status == 200
    assert body["ok"] is True
    assert body["state"]["glow"] == "completed"
    assert body["state"]["pending"] == []
    assert _device(body["state"], "garage", "garage_door")["is_on"] is False
    assert director.house.garage_open() is False

    _, state = _get_json(port, "/api/state")
    assert _device(state, "garage", "garage_door")["is_on"] is False


def test_unknown_request_approve_and_deny_return_404(server) -> None:
    _, _, port = server
    status, body = _post(port, "/api/requests/nope/approve", {})
    assert status == 404
    assert body == {"error": "unknown request"}

    status, body = _post(port, "/api/requests/nope/deny")
    assert status == 404
    assert body == {"error": "unknown request"}


def test_deny_over_http_dismisses_the_pending_request(server) -> None:
    _, _, port = server
    _, initial = _get_json(port, "/api/state")
    request_id = initial["pending"][0]["request_id"]

    status, body = _post(port, f"/api/requests/{request_id}/deny")

    assert status == 200
    assert body["ok"] is True
    assert body["state"]["glow"] == "idle"
    assert body["state"]["pending"] == []


def test_chat_over_http_turns_off_the_focused_light(server) -> None:
    _, _, port = server

    status, body = _post(port, "/api/chat", {"text": "turn that light off", "focus": "office"})

    assert status == 200
    assert body["ok"] is True
    assert _device(body["state"], "office", "office_light")["is_on"] is False
    assert body["state"]["conversation"][-2] == {"from": "user", "text": "turn that light off"}

    status, body = _post(port, "/api/chat", {"text": "turn that light off"})
    assert status == 200
    assert body["state"]["conversation"][-1] == {"from": "haven", "text": "Which room do you mean?"}


def test_demo_reset_over_http_reruns_the_scenario(server) -> None:
    _, _, port = server
    _, initial = _get_json(port, "/api/state")
    _post(port, f"/api/requests/{initial['pending'][0]['request_id']}/approve", {})

    status, body = _post(port, "/api/demo/reset")

    assert status == 200
    assert body["ok"] is True
    assert body["state"]["glow"] == "permission"
    assert len(body["state"]["pending"]) == 1
    assert _device(body["state"], "garage", "garage_door")["is_on"] is True


def test_sse_streams_initial_state_and_followup_events(server) -> None:
    _, _, port = server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    connection.request("GET", "/events")
    response = connection.getresponse()

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream"

    event, data = _read_event(response)
    assert event == "state"
    initial = json.loads(data)
    assert initial["glow"] == "permission"
    assert len(initial["pending"]) == 1

    status, _ = _post(port, "/api/chat", {"text": "turn that light off", "focus": "office"})
    assert status == 200

    seen_glow = False
    state_event = None
    for _ in range(6):
        event, data = _read_event(response)
        if event == "glow":
            seen_glow = True
        if event == "state":
            state_event = json.loads(data)
            if _device(state_event, "office", "office_light")["is_on"] is False:
                break
    assert seen_glow
    assert state_event is not None
    assert _device(state_event, "office", "office_light")["is_on"] is False
    connection.close()


def test_demo_reset_clears_a_critical_camera_scenario(server) -> None:
    _, director, port = server
    _, initial = _get_json(port, "/api/state")
    _post(port, f"/api/requests/{initial['pending'][0]['request_id']}/deny")

    status, body = _post(port, "/api/demo/camera-down")
    assert status == 200
    assert body["state"]["glow"] == "critical"
    assert body["state"]["glow_target"] == "driveway"
    assert body["state"]["status"]["line"] == "HAVEN can't see clearly"

    status, body = _post(port, "/api/demo/reset")
    assert status == 200
    assert body["state"]["glow"] == "permission"
    assert body["state"]["glow_target"] == "garage"
    assert director.house.camera_down() is False


def test_camera_down_then_up_over_http(server) -> None:
    _, director, port = server
    _, initial = _get_json(port, "/api/state")
    _post(port, f"/api/requests/{initial['pending'][0]['request_id']}/deny")

    status, body = _post(port, "/api/demo/camera-down")
    assert status == 200
    state = body["state"]
    texts = [entry["text"] for entry in state["conversation"]]
    assert "I can't confirm the driveway camera is up." in texts
    # Fail closed: the clip command never reached the execution adapter.
    assert [c for c in director.adapter.commands if c.service == "camera.record_clip"] == []

    status, body = _post(port, "/api/demo/camera-up")
    assert status == 200
    assert body["state"]["glow"] == "completed"
    assert body["state"]["glow_target"] == "driveway"
    clips = [c for c in director.adapter.commands if c.service == "camera.record_clip"]
    assert len(clips) == 1


def test_sse_glow_event_carries_the_target(server) -> None:
    _, _, port = server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    connection.request("GET", "/events")
    response = connection.getresponse()

    event, _ = _read_event(response)
    assert event == "state"

    status, _ = _post(port, "/api/chat", {"text": "turn that light off", "focus": "office"})
    assert status == 200

    seen: list[dict] = []
    for _ in range(6):
        event, data = _read_event(response)
        if event == "glow":
            seen.append(json.loads(data))
            if seen[-1].get("state") == "completed":
                break
    assert any(payload.get("target") == "office" for payload in seen)
    assert seen[-1] == {"state": "completed", "target": "office"}
    connection.close()


def test_state_carries_voice_and_wake_endpoint_engages(server) -> None:
    _, _, port = server
    _, state = _get_json(port, "/api/state")
    assert state["voice"] == {"state": "dormant", "mic": False}

    status, body = _post(port, "/api/voice/wake")

    assert status == 200
    assert body["ok"] is True
    assert body["state"]["voice"] == {"state": "wake", "mic": False}


def test_voice_endpoints_over_http_run_a_full_spoken_command(server) -> None:
    _, director, port = server
    status, body = _post(port, "/api/voice/wake")
    assert status == 200
    assert body["ok"] is True
    director.voice.expire_ack()

    status, body = _post(port, "/api/voice/utterance", {"text": "turn that light off"})

    assert status == 200
    assert body["ok"] is True
    state = body["state"]
    assert state["voice"] == {"state": "dormant", "mic": False}
    assert _device(state, "office", "office_light")["is_on"] is False
    assert {"from": "user", "text": "turn that light off"} in state["conversation"]

    # Refractory refusal: wake right after the interaction is ok:false with a
    # normal 200 envelope, and the voice state stays dormant.
    status, body = _post(port, "/api/voice/wake")
    assert status == 200
    assert body["ok"] is False
    assert body["state"]["voice"] == {"state": "dormant", "mic": False}

    # Utterance while dormant is refused too.
    status, body = _post(port, "/api/voice/utterance", {"text": "close the garage"})
    assert status == 200
    assert body["ok"] is False
    assert body["state"]["voice"] == {"state": "dormant", "mic": False}


def test_voice_cancel_endpoint_over_http(server) -> None:
    _, director, port = server
    _, initial = _get_json(port, "/api/state")
    _post(port, f"/api/requests/{initial['pending'][0]['request_id']}/deny")
    _post(port, "/api/voice/wake")
    director.voice.expire_ack()

    status, body = _post(port, "/api/voice/cancel")

    assert status == 200
    assert body["ok"] is True
    assert body["state"]["voice"] == {"state": "dormant", "mic": False}
    assert body["state"]["conversation"][-1] == {"from": "haven", "text": "Stopped."}

    # Cancel while dormant is an ok:false no-op.
    status, body = _post(port, "/api/voice/cancel")
    assert status == 200
    assert body["ok"] is False


def test_sse_state_events_carry_voice_transitions(server) -> None:
    _, _, port = server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    connection.request("GET", "/events")
    response = connection.getresponse()

    event, data = _read_event(response)
    assert event == "state"
    assert json.loads(data)["voice"] == {"state": "dormant", "mic": False}

    status, _ = _post(port, "/api/voice/wake")
    assert status == 200

    voice_seen = None
    for _ in range(4):
        event, data = _read_event(response)
        if event == "state":
            payload = json.loads(data)
            if payload["voice"]["state"] == "wake":
                voice_seen = payload["voice"]
                break
    assert voice_seen == {"state": "wake", "mic": False}
    connection.close()
