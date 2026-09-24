"""Native rooms/devices IPC adapter: same governed device-command path as the web surface."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _seed_installation(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    SetupConfigStore(data_dir / "haven.json").save(
        SetupConfig(completed=True, data_dir=str(data_dir), household_id="household-authoring")
    )
    (data_dir / "household.json").write_text(
        '{"version": 1, "rooms": [], "people": [], "contexts": []}',
        encoding="utf-8",
    )


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        try:
            yield instance, director
        finally:
            instance.server_close()


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _device(state: dict, device_id: str) -> dict:
    for room in state["rooms"]:
        for device in room["devices"]:
            if device["id"] == device_id:
                return device
    raise AssertionError(f"device {device_id} not present in state rooms")


def test_rooms_list_matches_the_web_rooms_shape(server) -> None:
    instance, _ = server
    response = _dispatch(instance, "rooms.list", {})

    assert response["ok"] is True
    rooms = response["result"]["rooms"]
    assert rooms == instance.director.state()["rooms"]
    for room in rooms:
        assert {"id", "name", "devices", "people", "camera"} <= set(room)
    office = _device(response["result"], "office_light")
    assert office["status"] == "observed"
    assert office["changed_by"] == "system"
    assert office["confidence"] == 1.0
    assert office["source"] == "demo.house"
    # The pending pool rides along so the native refresh button can repaint
    # the whole view from one call.
    assert response["result"]["pending"] == instance.director.state()["pending"]


def test_rooms_get_returns_one_room_and_rejects_unknown_ids(server) -> None:
    instance, _ = server
    listed = _dispatch(instance, "rooms.list", {})
    room_id = listed["result"]["rooms"][0]["id"]

    response = _dispatch(instance, "rooms.get", {"room_id": room_id})
    assert response["ok"] is True
    assert response["result"]["room"]["id"] == room_id

    missing = _dispatch(instance, "rooms.get", {"room_id": "attic"})
    assert missing["ok"] is False
    assert "unknown room" in missing["error"]

    blank = _dispatch(instance, "rooms.get", {"room_id": "  "})
    assert blank["ok"] is False
    assert "room_id" in blank["error"]


def test_light_turn_off_executes_through_the_director(server) -> None:
    instance, _ = server
    response = _dispatch(
        instance, "devices.command", {"device_id": "office_light", "service": "light.turn_off"}
    )

    assert response["ok"] is True
    result = response["result"]
    assert result["ok"] is True
    assert _device(result["state"], "office_light")["is_on"] is False
    assert _device(instance.director.state(), "office_light")["is_on"] is False


def test_set_brightness_applies_and_zero_turns_the_light_off(server) -> None:
    instance, _ = server
    response = _dispatch(
        instance,
        "devices.command",
        {"device_id": "office_light", "service": "light.set_brightness", "brightness_pct": 40},
    )

    assert response["ok"] is True
    office = _device(response["result"]["state"], "office_light")
    assert office["brightness_pct"] == 40
    assert office["is_on"] is True

    response = _dispatch(
        instance,
        "devices.command",
        {"device_id": "office_light", "service": "light.set_brightness", "brightness_pct": 0},
    )
    assert response["ok"] is True
    office = _device(response["result"]["state"], "office_light")
    assert office["brightness_pct"] == 0
    assert office["is_on"] is False


def test_garage_close_pends_then_requests_approve_executes(server) -> None:
    instance, _ = server
    prior = {item["request_id"] for item in instance.director.state()["pending"]}

    response = _dispatch(
        instance, "devices.command", {"device_id": "garage_door", "service": "cover.close"}
    )
    assert response["ok"] is True
    assert response["result"]["ok"] is True
    fresh = [
        item
        for item in response["result"]["state"]["pending"]
        if item["request_id"] not in prior
    ]
    assert len(fresh) == 1, "the guarded close pends a confirmation request"

    approved = _dispatch(instance, "requests.approve", {"request_id": fresh[0]["request_id"]})
    assert approved["ok"] is True
    state = approved["result"]["state"]
    assert "action_executed" in {row["event_type"] for row in state["activity"]}
    assert _device(state, "garage_door")["is_on"] is False


def test_requests_deny_releases_the_pending_request(server) -> None:
    instance, _ = server
    prior = {item["request_id"] for item in instance.director.state()["pending"]}
    _dispatch(instance, "devices.command", {"device_id": "garage_door", "service": "cover.close"})
    pending = instance.director.state()["pending"]
    fresh = [item for item in pending if item["request_id"] not in prior]
    assert len(fresh) == 1

    denied = _dispatch(instance, "requests.deny", {"request_id": fresh[0]["request_id"]})
    assert denied["ok"] is True
    assert fresh[0]["request_id"] not in {
        item["request_id"] for item in denied["result"]["state"]["pending"]
    }

    unknown = _dispatch(instance, "requests.deny", {"request_id": "request:nope"})
    assert unknown["ok"] is False
    assert "unknown request" in unknown["error"]
    unknown_approve = _dispatch(instance, "requests.approve", {"request_id": "request:nope"})
    assert unknown_approve["ok"] is False
    assert "unknown request" in unknown_approve["error"]


def test_director_refusals_ride_inside_the_result_envelope(server) -> None:
    instance, _ = server
    unknown = _dispatch(
        instance, "devices.command", {"device_id": "attic_light", "service": "light.turn_off"}
    )
    assert unknown["ok"] is True
    assert unknown["result"]["ok"] is False
    assert unknown["result"]["error"] == "unknown device"

    unsupported = _dispatch(
        instance, "devices.command", {"device_id": "office_light", "service": "cover.open"}
    )
    assert unsupported["ok"] is True
    assert unsupported["result"]["ok"] is False
    assert unsupported["result"]["error"] == "unsupported service"

    read_only = _dispatch(
        instance, "devices.command", {"device_id": "driveway_cam", "service": "light.turn_off"}
    )
    assert read_only["result"]["ok"] is False
    assert read_only["result"]["error"] == "unsupported service"


def test_malformed_commands_fail_closed_at_the_adapter(server) -> None:
    instance, _ = server
    missing_service = _dispatch(instance, "devices.command", {"device_id": "office_light"})
    assert missing_service["ok"] is False
    assert "service" in missing_service["error"]

    missing_device = _dispatch(instance, "devices.command", {"service": "light.turn_off"})
    assert missing_device["ok"] is False
    assert "device_id" in missing_device["error"]

    for bad in ("bright", None, True, 12.5):
        response = _dispatch(
            instance,
            "devices.command",
            {"device_id": "office_light", "service": "light.set_brightness", "brightness_pct": bad},
        )
        assert response["ok"] is False
        assert "brightness_pct" in response["error"]


@pytest.fixture()
def real_server():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        instance, _director = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            yield instance
        finally:
            instance.server_close()


def test_room_authoring_round_trip_and_shape(real_server) -> None:
    instance = real_server
    dispatcher = instance.build_ipc_dispatcher()

    added = dispatcher(request_message("req-add-room", "rooms.add", {"name": "Studio"}))
    assert added["ok"] is True
    assert added["result"]["ok"] is True
    assert any(
        room["room_id"] == "studio"
        for room in added["result"]["setup"]["household"]["rooms"]
    )
    # A declaration is valid with no device connected (spec section 36).
    listed = dispatcher(request_message("req-list", "rooms.list", {}))
    studio = next(room for room in listed["result"]["rooms"] if room["id"] == "studio")
    assert studio["devices"] == []
    assert studio["name"] == "Studio"
    detail = dispatcher(request_message("req-get", "rooms.get", {"room_id": "studio"}))
    assert detail["result"]["room"]["name"] == "Studio"

    renamed = dispatcher(
        request_message("req-rename", "rooms.rename", {"room_id": "studio", "name": "Workshop"})
    )
    assert renamed["ok"] is True
    assert renamed["result"]["ok"] is True
    assert any(
        room["room_id"] == "studio" and room["name"] == "Workshop"
        for room in renamed["result"]["setup"]["household"]["rooms"]
    )
    listed = dispatcher(request_message("req-list-2", "rooms.list", {}))
    assert next(room for room in listed["result"]["rooms"] if room["id"] == "studio")["name"] == "Workshop"

    removed = dispatcher(request_message("req-remove", "rooms.remove", {"room_id": "studio"}))
    assert removed["ok"] is True
    assert removed["result"]["ok"] is True
    listed = dispatcher(request_message("req-list-3", "rooms.list", {}))
    assert all(room["id"] != "studio" for room in listed["result"]["rooms"])
    gone = dispatcher(request_message("req-get-gone", "rooms.get", {"room_id": "studio"}))
    assert gone["ok"] is False
    assert "unknown room" in gone["error"]


def test_room_authoring_validation_and_idempotency(real_server) -> None:
    instance = real_server
    dispatcher = instance.build_ipc_dispatcher()

    blank = dispatcher(request_message("req-blank", "rooms.add", {"name": "  "}))
    assert blank["ok"] is True
    assert blank["result"]["ok"] is False
    assert "name" in blank["result"]["error"]

    first = dispatcher(request_message("req-first", "rooms.add", {"name": "Loft"}))
    assert first["result"]["ok"] is True
    # Re-declaring the same name is a harmless no-op.
    again = dispatcher(request_message("req-again", "rooms.add", {"name": "Loft"}))
    assert again["result"]["ok"] is True

    renamed = dispatcher(
        request_message("req-rename-unknown", "rooms.rename", {"room_id": "attic", "name": "Attic"})
    )
    assert renamed["result"]["ok"] is False
    assert renamed["result"]["error"] == "unknown room: attic"

    removed = dispatcher(request_message("req-remove-unknown", "rooms.remove", {"room_id": "attic"}))
    assert removed["result"]["ok"] is False
    assert removed["result"]["error"] == "unknown room: attic"
