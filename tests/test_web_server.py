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
    assert isinstance(state["revision"], int)
    for key in ("rooms", "contexts", "people", "pending", "conversation"):
        assert isinstance(state[key], list), key
    assert isinstance(state["status"], dict)

    office = _room(state, "office")
    assert office["name"] == "Office"
    assert office["people"] == ["Gerron"]
    light = _device(state, "office", "office_light")
    assert light["is_on"] is True
    assert light["brightness_pct"] == 70
    assert datetime.fromisoformat(light["observed_at"]).tzinfo is not None

    driveway = _room(state, "driveway")
    assert driveway["camera"] == {"id": "driveway_cam", "label": "Driveway", "motion": False}
    assert _room(state, "garage")["camera"] is None

    assert {context["id"] for context in state["contexts"]} == {"working_late", "vacation_mode"}
    assert state["people"] == [{"id": "gerron", "name": "Gerron", "room": "office"}]
    assert len(state["pending"]) == 1
    assert state["status"]["core"] == "Local Core"
    assert state["status"]["devices"] == 5
    assert state["status"]["line"] == "HAVEN needs your attention"


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
