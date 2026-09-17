"""WorldView enrichment: readable names, rooms, capabilities, attributes,
recent transitions, and the uncertainty flag -- all bounded and JSON-safe.
"""

import json
from datetime import datetime, timedelta, timezone

from haven.core.domain import (
    ChangeOrigin,
    DeviceState,
    DomainEvent,
    EventType,
    EvidenceStatus,
    PresenceState,
    WorldSnapshot,
)
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.intelligence.worldview import (
    WorldDeviceView,
    WorldPresenceView,
    WorldTransitionView,
    WorldView,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="office_light",
            device_type="light",
            provider_id="test",
            room="office",
            semantic_role="light",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
                CapabilityDescriptor(
                    "brightness", ControlClass.MEDIUM, writable=True, service="light.set_brightness"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="garage_door",
            device_type="cover",
            provider_id="test",
            room="garage",
            capabilities=(
                CapabilityDescriptor("close", ControlClass.GUARDED, writable=True, service="cover.close"),
            ),
        )
    )
    return registry


def _snapshot() -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="snap-1",
        household_id="household-1",
        captured_at=NOW,
        valid_until=NOW + timedelta(minutes=30),
        devices=(
            DeviceState(
                device_id="office_light",
                kind="light",
                room_id="office",
                is_on=True,
                brightness_pct=70,
                observed_at=NOW - timedelta(minutes=1),
                source="test",
                changed_by=ChangeOrigin.HUMAN,
                confidence=0.8,
            ),
            DeviceState(
                device_id="garage_door",
                kind="cover",
                room_id="garage",
                is_on=True,
                brightness_pct=None,
                observed_at=NOW - timedelta(minutes=18),
                source="test",
            ),
        ),
        presence=(
            PresenceState(
                person_id="resident-1",
                room_id="office",
                present=True,
                observed_at=NOW - timedelta(seconds=30),
                source="camera",
                confidence=1.0,
            ),
        ),
    )


def _events() -> tuple[DomainEvent, ...]:
    return (
        DomainEvent(
            event_id="evt-1",
            household_id="household-1",
            event_type=EventType.RULE_APPROVED,
            actor_id="resident-1",
            occurred_at=NOW - timedelta(minutes=5),
            payload=(("rule_id", "rule-1"),),
            correlation_id="rule-1",
            source="test",
        ),
        DomainEvent(
            event_id="evt-2",
            household_id="household-1",
            event_type=EventType.ACTION_EXECUTED,
            actor_id="resident-1",
            occurred_at=NOW - timedelta(minutes=2),
            payload=(("target_device_id", "garage_door"), ("action_id", "act-1")),
            correlation_id="direct-x",
            source="test",
        ),
        DomainEvent(
            event_id="evt-3",
            household_id="household-1",
            event_type=EventType.ACTION_BLOCKED,
            actor_id="resident-1",
            occurred_at=NOW - timedelta(minutes=1),
            payload=(("target_device_id", "office_light"), ("action_id", "act-2")),
            correlation_id="direct-y",
            source="test",
        ),
    )


# -- names, rooms, capabilities, attributes -----------------------------------


def test_projection_without_registry_or_names_falls_back_to_ids() -> None:
    view = WorldView.from_snapshot(_snapshot(), now=NOW)

    office = next(item for item in view.devices if item.device_id == "office_light")
    assert office.name == "light · office_light"  # role · id fallback
    assert office.room_name == "Office"  # title-cased room fallback
    assert office.capabilities == ()
    assert dict(office.attributes) == {"is_on": True, "brightness_pct": 70}
    garage = next(item for item in view.devices if item.device_id == "garage_door")
    assert dict(garage.attributes) == {"is_on": True}  # only what exists


def test_projection_with_registry_and_names_mapping() -> None:
    view = WorldView.from_snapshot(
        _snapshot(),
        now=NOW,
        device_registry=_registry(),
        names={"office_light": "Desk lamp", "office": "The Office"},
    )

    office = next(item for item in view.devices if item.device_id == "office_light")
    assert office.name == "Desk lamp"
    assert office.room_name == "The Office"
    assert office.capabilities == ("power", "brightness")
    garage = next(item for item in view.devices if item.device_id == "garage_door")
    assert garage.capabilities == ("close",)
    assert garage.name == "cover · garage_door"


# -- uncertainty flag ----------------------------------------------------------


def test_uncertain_flag_mirrors_confidence() -> None:
    view = WorldView.from_snapshot(_snapshot(), now=NOW)

    office = next(item for item in view.devices if item.device_id == "office_light")
    assert office.confidence == 0.8
    assert office.uncertain is True
    garage = next(item for item in view.devices if item.device_id == "garage_door")
    assert garage.confidence == 1.0
    assert garage.uncertain is False
    assert view.presence[0].uncertain is False
    tentative = WorldPresenceView(
        person_id="resident-1",
        room_id="office",
        present=True,
        confidence=0.6,
        observed_at=NOW.isoformat(),
        fresh=True,
    )
    assert tentative.uncertain is True


# -- recent transitions ---------------------------------------------------------


def _event(event_type: EventType, occurred_at: datetime, **payload) -> DomainEvent:
    return DomainEvent(
        event_id=f"evt-{occurred_at.timestamp()}",
        household_id="household-1",
        event_type=event_type,
        actor_id="resident-1",
        occurred_at=occurred_at,
        payload=tuple(sorted(payload.items())),
        correlation_id="c",
        source="test",
    )


def test_transitions_render_device_events_newest_first() -> None:
    view = WorldView.from_snapshot(
        _snapshot(), now=NOW, device_registry=_registry(), recent_events=_events()
    )

    # The rule_approved event is not device/state-related and is filtered out.
    assert view.transition_count == 2
    first, second = view.recent_transitions
    assert first.at == (NOW - timedelta(minutes=1)).isoformat()
    assert first.device_id == "office_light"
    assert first.room_id == "office"  # resolved through the device registry
    assert first.summary == "action_blocked by resident-1"
    assert second.device_id == "garage_door"
    assert second.room_id == "garage"
    assert second.summary == "action_executed by resident-1"


def test_transitions_bounded_to_the_newest_ten() -> None:
    events = tuple(
        _event(EventType.ACTION_EXECUTED, NOW - timedelta(minutes=index), target_device_id="garage_door")
        for index in range(15)
    )

    view = WorldView.from_snapshot(_snapshot(), now=NOW, recent_events=events)

    assert view.transition_count == 10
    assert view.truncated is True
    assert view.recent_transitions[0].at == NOW.isoformat()


def test_absent_recent_events_means_no_transitions() -> None:
    view = WorldView.from_snapshot(_snapshot(), now=NOW)

    assert view.recent_transitions == ()
    assert view.transition_count == 0
    assert view.truncated is False


# -- serialization ---------------------------------------------------------------


def test_json_round_trip_with_enrichment_is_lossless() -> None:
    view = WorldView.from_snapshot(
        _snapshot(),
        now=NOW,
        device_registry=_registry(),
        names={"office_light": "Desk lamp"},
        recent_events=_events(),
    )

    restored = WorldView.from_json(view.to_json())

    assert restored == view
    assert json.loads(restored.to_json()) == json.loads(view.to_json())
    device = restored.devices[0].to_dict()
    assert device["uncertain"] is True
    assert device["name"] == "Desk lamp"
    assert device["attributes"] == {"is_on": True, "brightness_pct": 70}


def test_from_dict_tolerates_pre_enrichment_payloads() -> None:
    """A dict written before the enrichment (no new keys) must still read."""

    legacy_device = {
        "device_id": "office_light",
        "room_id": "office",
        "kind": "light",
        "is_on": True,
        "brightness_pct": 70,
        "observed_at": NOW.isoformat(),
        "fresh": True,
        "changed_by": "human",
        "confidence": 0.8,
    }
    view = WorldView.from_dict(
        {
            "household_id": "household-1",
            "captured_at": NOW.isoformat(),
            "valid_until": (NOW + timedelta(minutes=30)).isoformat(),
            "devices": [legacy_device],
            "presence": [],
            "contexts": [],
            "truncated": False,
        }
    )

    device = view.devices[0]
    assert device.name is None  # fallback renders at to_dict time
    assert device.room_name is None
    assert device.capabilities == ()
    # Legacy top-level is_on/brightness_pct flow into the attributes bag.
    assert dict(device.attributes) == {"is_on": True, "brightness_pct": 70}
    assert device.uncertain is True  # derived from the carried confidence
    assert view.recent_transitions == ()
    assert view.transition_count == 0
    rendered = device.to_dict()
    assert rendered["name"] == "office_light"
    assert rendered["room_name"] == "Office"
    assert rendered["capabilities"] == []
    assert rendered["attributes"] == {"is_on": True, "brightness_pct": 70}


def test_transition_view_validates_and_round_trips() -> None:
    transition = WorldTransitionView(
        at=NOW.isoformat(),
        device_id="garage_door",
        room_id="garage",
        summary="action_executed by resident-1",
    )

    restored = WorldTransitionView.from_dict(transition.to_dict())

    assert restored == transition
    assert WorldTransitionView.from_dict({**transition.to_dict(), "device_id": None}).device_id is None
