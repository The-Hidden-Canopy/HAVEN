"""WorldProvider implementations: simulated house and resilient HA observe.

The HomeAssistantWorldProvider tests use a plain fake fetch_states() client --
no urlopen mock and no network anywhere -- in the same idiom as
test_home_assistant_observer.py.
"""

from datetime import datetime, timedelta, timezone

from haven.core.domain import EvidenceStatus
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.integrations.home_assistant import HomeAssistantObserver, HomeAssistantWorldProvider
from haven.web.demo import HOUSEHOLD_ID, SimulatedHouse, SimulatedWorldProvider

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
            device_id="light.living_room",
            device_type="light",
            provider_id="home_assistant",
            room="living_room",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, writable=True, service="light.turn_on"
                ),
            ),
        )
    )
    return registry


def _provider(client) -> HomeAssistantWorldProvider:
    return HomeAssistantWorldProvider(
        observer=HomeAssistantObserver(
            client=client,
            device_registry=_registry(),
            household_id="household-a",
        ),
        empty_household_id="household-a",
    )


def test_simulated_world_provider_observes_the_house_snapshot():
    house = SimulatedHouse(now=NOW)
    provider = SimulatedWorldProvider(house)

    snapshot = provider.observe(NOW)

    assert provider.house is house
    assert snapshot.household_id == HOUSEHOLD_ID
    device_ids = {device.device_id for device in snapshot.devices}
    assert {"office_light", "garage_door", "driveway_cam"} <= device_ids
    assert snapshot.presence[0].present is True


def test_ha_world_provider_returns_devices_from_ha():
    client = _FakeClient(states=(_entity("light.living_room", "on", attributes={"brightness": 128}),))
    snapshot = _provider(client).observe(NOW)

    assert snapshot.household_id == "household-a"
    assert [device.device_id for device in snapshot.devices] == ["light.living_room"]
    assert snapshot.devices[0].is_on is True
    assert snapshot.devices[0].brightness_pct == 50


def test_ha_world_provider_returns_last_good_snapshot_when_fetch_fails():
    client = _FakeClient(states=(_entity("light.living_room", "on"),))
    provider = _provider(client)
    good = provider.observe(NOW)
    assert good.devices[0].status == EvidenceStatus.OBSERVED

    client._error = ConnectionError("network down")
    fallback = provider.observe(NOW + timedelta(seconds=30))

    # Same values (UI continuity: "last known: on, 30s ago") but never the
    # same object -- a failed refresh must not be indistinguishable from a
    # fresh, successful one to whatever reads the snapshot next.
    assert fallback is not good
    assert fallback.devices[0].device_id == good.devices[0].device_id
    assert fallback.devices[0].is_on == good.devices[0].is_on
    assert fallback.devices[0].observed_at == good.devices[0].observed_at
    # The load-bearing change: authority's freshness check only ever treats
    # OBSERVED as fresh, so this must fail closed exactly like stale evidence.
    assert fallback.devices[0].status == EvidenceStatus.FALLBACK
    assert fallback.evidence_problem(
        status=fallback.devices[0].status,
        observed_at=fallback.devices[0].observed_at,
        confidence=fallback.devices[0].confidence,
        at=NOW + timedelta(seconds=30),
        minimum_confidence=1.0,
    ) is not None

    # A later successful poll clears the fallback marking entirely -- this
    # is not a permanent scar on the cached snapshot.
    client._error = None
    recovered = provider.observe(NOW + timedelta(seconds=60))
    assert recovered.devices[0].status == EvidenceStatus.OBSERVED


def test_ha_world_provider_fallback_keeps_unavailable_devices_unavailable():
    client = _FakeClient(
        states=(
            _entity("light.living_room", "unavailable"),
        )
    )
    provider = _provider(client)
    good = provider.observe(NOW)
    assert good.devices[0].status == EvidenceStatus.UNAVAILABLE

    client._error = ConnectionError("network down")
    fallback = provider.observe(NOW + timedelta(seconds=30))
    # Already-UNAVAILABLE evidence keeps its more specific status rather
    # than being overwritten to the vaguer FALLBACK.
    assert fallback.devices[0].status == EvidenceStatus.UNAVAILABLE


def test_ha_world_provider_returns_empty_valid_snapshot_before_first_good():
    provider = _provider(_FakeClient(error=ConnectionError("network down")))

    snapshot = provider.observe(NOW)

    assert snapshot.household_id == "household-a"
    assert snapshot.captured_at == NOW
    assert snapshot.valid_until == NOW + timedelta(minutes=2)
    assert snapshot.devices == ()
    assert snapshot.presence == ()
    assert snapshot.contexts == ()
