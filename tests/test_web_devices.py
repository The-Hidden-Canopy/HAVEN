"""Governed device-command endpoint: provenance in the state payload, direct
light control through authority, and the guarded garage approve flow."""

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


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else ""
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def _device(state: dict, device_id: str) -> dict:
    for room in state["rooms"]:
        for device in room["devices"]:
            if device["id"] == device_id:
                return device
    raise AssertionError(f"device {device_id} not present in state rooms")


def _command(port: int, device_id: str, service: str, **body: int) -> tuple[int, dict]:
    payload = {"service": service, **body}
    return _post(port, f"/api/devices/{device_id}/command", payload)


def test_state_devices_carry_provenance(server) -> None:
    _, _, port = server
    status, state = _get_json(port, "/api/state")
    assert status == 200
    office = _device(state, "office_light")
    assert office["status"] == "observed"
    assert office["changed_by"] == "system"
    assert office["confidence"] == 1.0
    assert office["source"] == "demo.house"
    # The garage door state is 18 minutes old but still observed evidence.
    garage = _device(state, "garage_door")
    assert garage["status"] == "observed"
    assert garage["source"] == "demo.house"


def test_light_turn_off_executes_through_authority(server) -> None:
    _, _, port = server
    status, body = _command(port, "office_light", "light.turn_off")
    assert status == 200
    assert body["ok"] is True
    assert _device(body["state"], "office_light")["is_on"] is False

    _, state = _get_json(port, "/api/state")
    assert _device(state, "office_light")["is_on"] is False


def test_garage_close_pends_then_approves_into_execution(server) -> None:
    _, _, port = server
    # The demo scenario already pends one garage request; diff the pending
    # set so the command's own request is the one approved.
    _, before = _get_json(port, "/api/state")
    prior = {item["request_id"] for item in before["pending"]}

    status, body = _command(port, "garage_door", "cover.close")
    assert status == 200
    assert body["ok"] is True
    fresh = [
        item for item in body["state"]["pending"] if item["request_id"] not in prior
    ]
    assert len(fresh) == 1, "the guarded close pends a confirmation request"

    status, approved = _post(port, f"/api/requests/{fresh[0]['request_id']}/approve", {})
    assert status == 200
    assert approved["ok"] is True
    assert "action_executed" in {row["event_type"] for row in approved["state"]["activity"]}
    assert _device(approved["state"], "garage_door")["is_on"] is False

    _, state = _get_json(port, "/api/state")
    assert _device(state, "garage_door")["is_on"] is False


def test_cover_open_on_a_light_is_unsupported(server) -> None:
    _, _, port = server
    status, body = _command(port, "office_light", "cover.open")
    assert status == 400
    assert body["ok"] is False
    assert body["error"] == "unsupported service"


def test_unknown_device_is_rejected(server) -> None:
    _, _, port = server
    status, body = _command(port, "attic_light", "light.turn_off")
    assert status == 400
    assert body["ok"] is False
    assert body["error"] == "unknown device"


def test_set_brightness_applies_and_zero_turns_the_light_off(server) -> None:
    _, _, port = server
    status, body = _command(port, "office_light", "light.set_brightness", brightness_pct=40)
    assert status == 200
    assert body["ok"] is True
    office = _device(body["state"], "office_light")
    assert office["brightness_pct"] == 40
    assert office["is_on"] is True

    status, body = _command(port, "office_light", "light.set_brightness", brightness_pct=0)
    assert status == 200
    assert body["ok"] is True
    office = _device(body["state"], "office_light")
    assert office["brightness_pct"] == 0
    assert office["is_on"] is False


def test_read_only_device_rejects_the_service(server) -> None:
    _, _, port = server
    status, body = _command(port, "driveway_cam", "light.turn_off")
    assert status == 400
    assert body["ok"] is False
    assert body["error"] == "unsupported service"


def test_set_brightness_requires_an_integer_percentage(server) -> None:
    _, _, port = server
    status, body = _command(port, "office_light", "light.set_brightness")
    assert status == 400
    assert body["ok"] is False

    status, body = _post(
        port,
        "/api/devices/office_light/command",
        {"service": "light.set_brightness", "brightness_pct": "bright"},
    )
    assert status == 400
    assert body["ok"] is False


def test_missing_service_field_is_a_400(server) -> None:
    _, _, port = server
    status, body = _post(port, "/api/devices/office_light/command", {})
    assert status == 400
    assert body["ok"] is False
