"""HomeAssistantObserver: one-shot HA fetch -> WorldSnapshot assembly.

The observer takes any object with fetch_states(), so these tests use a
plain fake client -- no urlopen mock and no network anywhere. The final test
closes the loop: an observed snapshot drives a real rule through
HavenRuntime's authority path, with execution recorded by the fixture
adapter.
"""

from datetime import datetime, timedelta, timezone

import pytest

from haven.core.domain import ActionKind, EvidenceStatus, RoleTier, RuleDraft
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.integrations.home_assistant import (
    ContextSource,
    FixtureHomeAssistant,
    HomeAssistantObserver,
    HomeAssistantStateError,
    PresenceSource,
)
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime
from test_vertical_slice import _principal

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
CHANGED = "2026-09-16T19:59:30+00:00"


class _FakeClient:
    def __init__(self, states=(), error: Exception | None = None) -> None:
        self._states = tuple(states)
        self._error = error

    def fetch_states(self):
        if self._error is not None:
            raise self._error
        return self._states


def _entity(entity_id, state, *, attributes=None, last_changed=CHANGED):
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {},
        "last_changed": last_changed,
    }


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
    return registry


def _observer(client, **kwargs) -> HomeAssistantObserver:
    return HomeAssistantObserver(
        client=client,
        device_registry=_registry(),
        household_id="household-a",
        **kwargs,
    )


def test_observe_assembles_snapshot_window_and_devices():
    client = _FakeClient([_entity("light.bedroom_lights", "on", attributes={"brightness": 255})])
    snapshot = _observer(client).observe(now=NOW)

    assert snapshot.household_id == "household-a"
    assert snapshot.captured_at == NOW
    assert snapshot.valid_until == NOW + timedelta(minutes=2)
    (device,) = snapshot.devices
    assert device.device_id == "light.bedroom_lights"
    assert device.is_on is True
    assert device.brightness_pct == 100
    assert snapshot.presence == ()
    assert snapshot.contexts == ()


def test_presence_sources_map_home_not_home_and_unavailable():
    client = _FakeClient(
        [
            _entity("binary_sensor.bedroom_occupancy", "on"),
            _entity("binary_sensor.office_occupancy", "off"),
            _entity("binary_sensor.kitchen_occupancy", "unavailable"),
        ]
    )
    observer = _observer(
        client,
        presence_sources=(
            PresenceSource(entity_id="binary_sensor.bedroom_occupancy", person_id="gerron", room_id="bedroom"),
            PresenceSource(entity_id="binary_sensor.office_occupancy", person_id="gerron", room_id="office"),
            PresenceSource(entity_id="binary_sensor.kitchen_occupancy", person_id="gerron", room_id="kitchen"),
        ),
    )
    snapshot = observer.observe(now=NOW)

    by_room = {item.room_id: item for item in snapshot.presence}
    assert by_room["bedroom"].present is True
    assert by_room["bedroom"].status == EvidenceStatus.OBSERVED
    assert by_room["office"].present is False
    assert by_room["office"].status == EvidenceStatus.OBSERVED
    assert by_room["kitchen"].status == EvidenceStatus.UNAVAILABLE


def test_uninterpretable_presence_state_and_bad_time_are_dropped():
    client = _FakeClient(
        [
            _entity("person.gerron", "work"),  # custom zone name: not taught, not guessed
            _entity("binary_sensor.bedroom_occupancy", "on", last_changed="not-a-timestamp"),
        ]
    )
    observer = _observer(
        client,
        presence_sources=(
            PresenceSource(entity_id="person.gerron", person_id="gerron", room_id="home"),
            PresenceSource(entity_id="binary_sensor.bedroom_occupancy", person_id="gerron", room_id="bedroom"),
        ),
    )
    snapshot = observer.observe(now=NOW)

    assert snapshot.presence == ()


def test_context_sources_map_on_off_and_unavailable():
    client = _FakeClient(
        [
            _entity("binary_sensor.working_late", "on"),
            _entity("binary_sensor.guest_mode", "off"),
            _entity("binary_sensor.vacation_mode", "unavailable"),
        ]
    )
    observer = _observer(
        client,
        context_sources=(
            ContextSource(entity_id="binary_sensor.working_late", context_id="working_late"),
            ContextSource(entity_id="binary_sensor.guest_mode", context_id="guest_mode"),
            ContextSource(entity_id="binary_sensor.vacation_mode", context_id="vacation_mode"),
        ),
    )
    snapshot = observer.observe(now=NOW)

    by_id = {item.context_id: item for item in snapshot.contexts}
    assert by_id["working_late"].active is True
    assert by_id["guest_mode"].active is False
    assert by_id["vacation_mode"].status == EvidenceStatus.UNAVAILABLE


def test_undeclared_entities_are_ignored():
    client = _FakeClient(
        [
            _entity("sensor.random_power_meter", "42"),
            _entity("light.bedroom_lights", "on"),
        ]
    )
    snapshot = _observer(client).observe(now=NOW)

    assert len(snapshot.devices) == 1
    assert snapshot.presence == ()
    assert snapshot.contexts == ()


def test_fetch_failure_raises_rather_than_fabricating_an_empty_snapshot():
    client = _FakeClient(error=HomeAssistantStateError("connection_error:refused"))

    with pytest.raises(HomeAssistantStateError):
        _observer(client).observe(now=NOW)


def test_snapshot_ttl_must_be_positive():
    with pytest.raises(ValueError):
        _observer(_FakeClient(), snapshot_ttl=timedelta(0))


def test_observed_snapshot_drives_a_rule_through_the_authority_path():
    """Close the loop: observe -> approve -> run_rule -> fixture execution."""
    resident = _principal(actor_id="gerron")
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    client = _FakeClient(
        [
            _entity("binary_sensor.bedroom_occupancy", "on"),
            _entity("binary_sensor.working_late", "on"),
            _entity("light.bedroom_lights", "on", attributes={"brightness": 204}),
        ]
    )
    observer = _observer(
        client,
        presence_sources=(
            PresenceSource(entity_id="binary_sensor.bedroom_occupancy", person_id="gerron", room_id="bedroom"),
        ),
        context_sources=(ContextSource(entity_id="binary_sensor.working_late", context_id="working_late"),),
    )
    store = HavenStore(household_id="household-a")
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(store=store, intelligence_provider=ScriptedIntelligenceProvider(), home_assistant=adapter)

    draft = RuleDraft(
        draft_id="draft-observed",
        household_id="household-a",
        proposed_by="gerron",
        source_text="cap bedroom lights at 20% when I'm working late",
        interpretation="Set bedroom lights to 20 percent when gerron is present and working_late is active.",
        trigger_person_id="gerron",
        trigger_room_id="bedroom",
        required_context="working_late",
        action_kind=ActionKind.SET_LIGHT_BRIGHTNESS,
        target_device_id="light.bedroom_lights",
        parameters=(("brightness_pct", 20),),
    )
    rule = runtime.propose_draft(draft, principal=resident, now=NOW)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Approve the late-work light cap against observed state.",
        now=NOW + timedelta(seconds=30),
    )

    snapshot = observer.observe(now=NOW + timedelta(minutes=1))
    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=snapshot,
        justification="Apply the approved rule against freshly observed HA state.",
        now=NOW + timedelta(minutes=1),
    )

    assert receipt.outcome == "executed"
    assert len(adapter.commands) == 1
    assert adapter.commands[0].target_device_id == "light.bedroom_lights"
    assert adapter.commands[0].service == "light.turn_on"
