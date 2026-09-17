"""Serialization contract for the web surface: ISO datetimes, enum values,
RoleTier names, and lists (never tuples).
"""

from datetime import datetime, timedelta, timezone

from haven.core.domain import DecisionStatus, DeviceState, EvidenceStatus, RoleTier
from haven.web import serialize
from haven.web.demo import PendingRequest

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _device() -> DeviceState:
    return DeviceState(
        device_id="office_light",
        kind="light",
        room_id="office",
        is_on=True,
        brightness_pct=70,
        observed_at=NOW,
        source="demo.house",
    )


def test_to_json_value_maps_enums_datetimes_tuples_and_role_tiers() -> None:
    assert serialize.to_json_value(DecisionStatus.ALLOW) == "allow"
    assert serialize.to_json_value(NOW) == NOW.isoformat()
    assert isinstance(serialize.to_json_value((1, 2)), list)
    assert serialize.to_json_value((1, 2)) == [1, 2]
    assert serialize.to_json_value(RoleTier.OWNER) == "OWNER"
    assert serialize.to_json_value({"tiers": (RoleTier.ADMIN,)}) == {"tiers": ["ADMIN"]}


def test_device_to_dict_uses_iso_datetime_and_plain_types() -> None:
    payload = serialize.device_to_dict(_device(), role="light")
    assert payload["observed_at"] == NOW.isoformat()
    assert payload == {
        "id": "office_light",
        "role": "light",
        "is_on": True,
        "brightness_pct": 70,
        "observed_at": NOW.isoformat(),
    }


def test_room_person_context_and_status_dicts_are_lists() -> None:
    room = serialize.room_to_dict(
        room_id="office",
        name="Office",
        devices=[serialize.device_to_dict(_device(), role="light")],
        people=["Gerron"],
        camera=None,
    )
    assert room["devices"][0]["id"] == "office_light"
    assert isinstance(room["devices"], list)
    assert isinstance(room["people"], list)
    assert room["camera"] is None

    camera = serialize.camera_to_dict(camera_id="driveway_cam", label="Driveway", motion=False)
    assert camera == {"id": "driveway_cam", "label": "Driveway", "motion": False}

    person = serialize.person_to_dict(person_id="gerron", name="Gerron", room="office")
    assert person == {"id": "gerron", "name": "Gerron", "room": "office"}

    context = serialize.context_to_dict(context_id="working_late", label="Working late", active=True)
    assert context == {"id": "working_late", "label": "Working late", "active": True}

    status = serialize.status_to_dict(devices=5, people=1, line="Everything nominal")
    assert status == {"core": "Local Core", "devices": 5, "people": 1, "line": "Everything nominal"}


def test_pending_to_dict_emits_iso_expiry() -> None:
    pending = PendingRequest(
        request_id="request-1",
        rule_id="rule-1",
        title="Close the garage door?",
        detail="It has been open 18 minutes. No motion detected nearby.",
        expires_at=NOW + timedelta(minutes=5),
    )
    payload = serialize.pending_to_dict(pending)
    assert payload["expires_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert payload["request_id"] == "request-1"
    assert payload["title"] == "Close the garage door?"


def test_message_and_state_dicts_match_the_wire_shape() -> None:
    message = serialize.message_to_dict(sender="haven", text="Want me to close it?")
    assert message == {"from": "haven", "text": "Want me to close it?"}

    state = serialize.state_to_dict(
        glow="permission",
        revision=3,
        rooms=[
            serialize.room_to_dict(
                room_id="office",
                name="Office",
                devices=[serialize.device_to_dict(_device(), role="light")],
                people=["Gerron"],
                camera=None,
            )
        ],
        contexts=[serialize.context_to_dict(context_id="working_late", label="Working late", active=True)],
        people=[serialize.person_to_dict(person_id="gerron", name="Gerron", room="office")],
        pending=[],
        conversation=[message],
        status=serialize.status_to_dict(devices=5, people=1, line="HAVEN needs your attention"),
    )
    assert state["glow"] == "permission"
    assert state["revision"] == 3
    for key in ("rooms", "contexts", "people", "pending", "conversation"):
        assert isinstance(state[key], list)
    assert state["status"]["line"] == "HAVEN needs your attention"
