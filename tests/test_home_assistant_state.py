"""device_states_from_ha: HA state dicts -> Haven DeviceState evidence.

The mapping is pure (no I/O), so these tests need no urlopen mock. The load-
bearing rules: only registry-known entities are mapped, evidence without a
parseable observation time is dropped rather than guessed at, and HA's
"unavailable" becomes EvidenceStatus.UNAVAILABLE -- which the authority
engine already treats as unable to authorize anything.
"""

from datetime import datetime, timezone

from haven.core.domain import EvidenceStatus
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
