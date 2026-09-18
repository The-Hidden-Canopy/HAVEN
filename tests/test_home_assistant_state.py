"""device_states_from_ha: HA state dicts -> Haven DeviceState evidence.

The mapping is pure (no I/O), so these tests need no urlopen mock. The load-
bearing rules: only registry-known entities are mapped, evidence without a
parseable observation time is dropped rather than guessed at, and HA's
"unavailable" becomes EvidenceStatus.UNAVAILABLE -- which the authority
engine already treats as unable to authorize anything.
"""

from datetime import datetime, timezone

from haven.core.domain import CoverState, EvidenceStatus, LockState
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.integrations.home_assistant import device_states_from_ha

UTC = timezone.utc
CHANGED = "2026-09-16T20:00:00.123456+00:00"
CHANGED_AT = datetime(2026, 9, 16, 20, 0, 0, 123456, tzinfo=UTC)


def _registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="light.bedroom_lights",
            device_type="light",
            provider_id="home_assistant",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="light.turn_on"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="switch.bedroom_lamp_plug",
            device_type="switch",
            semantic_role="light",
            provider_id="home_assistant",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="switch.turn_on"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="cover.garage_door",
            device_type="cover",
            provider_id="home_assistant",
            room="garage",
            capabilities=(
                CapabilityDescriptor(name="open", control_class=ControlClass.GUARDED, writable=True, service="cover.open"),
                CapabilityDescriptor(name="close", control_class=ControlClass.GUARDED, writable=True, service="cover.close"),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="lock.front_door",
            device_type="lock",
            provider_id="home_assistant",
            room="entry",
            capabilities=(
                CapabilityDescriptor(name="lock", control_class=ControlClass.GUARDED, writable=True, service="lock.lock"),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="climate.living_room",
            device_type="thermostat",
            provider_id="home_assistant",
            room="living_room",
            capabilities=(
                CapabilityDescriptor(
                    name="temperature", control_class=ControlClass.MEDIUM, writable=True, service="climate.set_temperature"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="camera.driveway",
            device_type="camera",
            provider_id="home_assistant",
            room="driveway",
            capabilities=(CapabilityDescriptor(name="live_stream", control_class=ControlClass.READ, readable=True),),
        )
    )
    return registry


def _state(entity_id, state, *, attributes=None, last_changed=CHANGED):
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {},
        "last_changed": last_changed,
    }


def test_on_light_maps_brightness_to_percent():
    (device,) = device_states_from_ha(
        [_state("light.bedroom_lights", "on", attributes={"brightness": 128})],
        _registry(),
    )

    assert device.device_id == "light.bedroom_lights"
    assert device.kind == "light"
    assert device.room_id == "bedroom"
    assert device.is_on is True
    assert device.brightness_pct == 50
    assert device.status == EvidenceStatus.OBSERVED
    assert device.source == "home_assistant.rest"
    assert device.observed_at == CHANGED_AT


def test_off_switch_maps_to_is_on_false_with_no_brightness():
    (device,) = device_states_from_ha(
        [_state("switch.bedroom_lamp_plug", "off")],
        _registry(),
    )

    assert device.is_on is False
    assert device.brightness_pct is None
    assert device.status == EvidenceStatus.OBSERVED


def test_unavailable_entity_maps_to_unavailable_evidence():
    (device,) = device_states_from_ha(
        [_state("light.bedroom_lights", "unavailable")],
        _registry(),
    )

    assert device.status == EvidenceStatus.UNAVAILABLE
    assert device.is_on is None


def test_unregistered_entities_are_skipped():
    assert device_states_from_ha([_state("light.unregistered", "on")], _registry()) == ()


def test_missing_or_unparseable_observation_time_is_dropped_not_guessed():
    states = [
        _state("light.bedroom_lights", "on", last_changed=None),
        _state("switch.bedroom_lamp_plug", "on", last_changed="not-a-timestamp"),
    ]

    assert device_states_from_ha(states, _registry()) == ()


def test_unusual_states_map_to_unknown_on_off_but_still_observed():
    (device,) = device_states_from_ha(
        [_state("switch.bedroom_lamp_plug", "idle")],
        _registry(),
    )

    assert device.is_on is None
    assert device.status == EvidenceStatus.OBSERVED


def test_light_and_switch_preserve_raw_state():
    (device,) = device_states_from_ha([_state("light.bedroom_lights", "on")], _registry())
    assert device.raw_state == "on"


def test_open_cover_maps_to_cover_state_open_never_none():
    (device,) = device_states_from_ha([_state("cover.garage_door", "open")], _registry())

    assert device.cover_state == CoverState.OPEN
    assert device.is_on is True
    assert device.raw_state == "open"
    assert device.status == EvidenceStatus.OBSERVED


def test_closed_cover_maps_to_cover_state_closed():
    (device,) = device_states_from_ha([_state("cover.garage_door", "closed")], _registry())

    assert device.cover_state == CoverState.CLOSED
    assert device.is_on is False


def test_transitional_cover_states_are_neither_on_nor_off():
    for raw in ("opening", "closing"):
        (device,) = device_states_from_ha([_state("cover.garage_door", raw)], _registry())
        assert device.cover_state == CoverState(raw)
        # A cover mid-travel is a real, known state -- not the same "no
        # evidence" None that missing/unavailable evidence produces.
        assert device.is_on is None
        assert device.raw_state == raw
        assert device.status == EvidenceStatus.OBSERVED


def test_unavailable_cover_still_reports_unavailable_not_a_guessed_state():
    (device,) = device_states_from_ha([_state("cover.garage_door", "unavailable")], _registry())

    assert device.status == EvidenceStatus.UNAVAILABLE
    assert device.cover_state is None
    assert device.raw_state == "unavailable"


def test_locked_and_unlocked_map_to_lock_state():
    (locked,) = device_states_from_ha([_state("lock.front_door", "locked")], _registry())
    assert locked.lock_state == LockState.LOCKED

    (unlocked,) = device_states_from_ha([_state("lock.front_door", "unlocked")], _registry())
    assert unlocked.lock_state == LockState.UNLOCKED


def test_climate_maps_mode_and_temperatures():
    (device,) = device_states_from_ha(
        [
            _state(
                "climate.living_room",
                "heat",
                attributes={"current_temperature": 68.5, "temperature": 71},
            )
        ],
        _registry(),
    )

    assert device.climate_mode == "heat"
    assert device.current_temperature == 68.5
    assert device.target_temperature == 71.0
    assert device.raw_state == "heat"
    # Climate is not a boolean device: the on/off fields stay unused.
    assert device.is_on is None


def test_camera_reports_availability_not_a_boolean_power_state():
    (device,) = device_states_from_ha([_state("camera.driveway", "idle")], _registry())
    assert device.camera_available is True
    assert device.is_on is None

    (down,) = device_states_from_ha([_state("camera.driveway", "unavailable")], _registry())
    assert down.status == EvidenceStatus.UNAVAILABLE
    assert down.camera_available is False
