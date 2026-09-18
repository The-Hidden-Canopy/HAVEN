"""`CompositeObserver`: fusing N `ObservationProvider`s into one WorldSnapshot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from haven.core.domain import ContextState, DeviceState, EvidenceStatus, PresenceState
from haven.integrations.home_assistant.world import HomeAssistantWorldProvider
from haven.perception.observation import FixtureObservationProvider
from haven.providers.world import CompositeObserver

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _presence(person="gerron", room="office", present=True, confidence=1.0, source="ble") -> PresenceState:
    return PresenceState(
        person_id=person, room_id=room, present=present, observed_at=NOW, source=source, confidence=confidence
    )


def _context(context_id="working_late", active=True, confidence=1.0, source="calendar") -> ContextState:
    return ContextState(context_id=context_id, active=active, observed_at=NOW, source=source, confidence=confidence)


def _device(device_id="light.office", is_on=True, observed_at=NOW, source="zigbee") -> DeviceState:
    return DeviceState(
        device_id=device_id,
        kind="light",
        room_id="office",
        is_on=is_on,
        brightness_pct=None,
        observed_at=observed_at,
        source=source,
    )


def test_observe_with_no_providers_returns_an_empty_snapshot():
    observer = CompositeObserver(providers=(), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    assert snapshot.household_id == "house-1"
    assert snapshot.presence == ()
    assert snapshot.contexts == ()
    assert snapshot.devices == ()


def test_a_single_providers_readings_pass_through_fused():
    provider = FixtureObservationProvider((_presence(), _context(), _device()))
    observer = CompositeObserver(providers=(provider,), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    assert len(snapshot.presence) == 1
    assert snapshot.presence[0].person_id == "gerron"
    assert snapshot.presence[0].source == "composite.fused"
    assert len(snapshot.contexts) == 1
    assert len(snapshot.devices) == 1


def test_agreeing_presence_from_two_providers_combines_confidence():
    a = FixtureObservationProvider((_presence(confidence=0.6, source="ble"),))
    b = FixtureObservationProvider((_presence(confidence=0.6, source="wifi"),))
    observer = CompositeObserver(providers=(a, b), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    assert len(snapshot.presence) == 1
    # noisy-OR: 1 - (1-0.6)(1-0.6) = 0.84
    assert snapshot.presence[0].confidence == pytest.approx(0.84)


def test_disagreeing_presence_is_dropped_not_guessed():
    a = FixtureObservationProvider((_presence(present=True, source="ble"),))
    b = FixtureObservationProvider((_presence(present=False, source="wifi"),))
    observer = CompositeObserver(providers=(a, b), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    assert snapshot.presence == ()


def test_disagreeing_context_is_dropped_not_guessed():
    a = FixtureObservationProvider((_context(active=True, source="calendar"),))
    b = FixtureObservationProvider((_context(active=False, source="manual"),))
    observer = CompositeObserver(providers=(a, b), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    assert snapshot.contexts == ()


def test_unavailable_presence_is_excluded_from_fusion():
    unavailable = PresenceState(
        person_id="gerron",
        room_id="office",
        present=False,
        observed_at=NOW,
        source="ble",
        status=EvidenceStatus.UNAVAILABLE,
    )
    provider = FixtureObservationProvider((unavailable,))
    observer = CompositeObserver(providers=(provider,), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    # Not fused into a false "observed absent" -- simply not present in the
    # snapshot, the same "no reading" a caller already treats as fail-closed.
    assert snapshot.presence == ()


def test_device_state_is_not_fused_the_most_recent_source_wins():
    older = _device(is_on=False, observed_at=NOW - timedelta(minutes=1), source="zigbee")
    newer = _device(is_on=True, observed_at=NOW, source="matter")
    provider_a = FixtureObservationProvider((older,))
    provider_b = FixtureObservationProvider((newer,))
    observer = CompositeObserver(providers=(provider_a, provider_b), household_id="house-1")
    snapshot = observer.observe(now=NOW)
    assert len(snapshot.devices) == 1
    assert snapshot.devices[0].is_on is True
    assert snapshot.devices[0].source == "matter"


def test_composite_observer_plugs_into_the_generic_world_provider_wrapper():
    # HomeAssistantWorldProvider is generic over anything satisfying
    # observe(now=...) -- a composite of community providers gets the exact
    # same cached/degrade-to-fallback behavior a Home Assistant household does.
    provider = FixtureObservationProvider((_presence(),))
    observer = CompositeObserver(providers=(provider,), household_id="house-1")
    world = HomeAssistantWorldProvider(observer=observer, empty_household_id="house-1")
    snapshot = world.observe(NOW)
    assert len(snapshot.presence) == 1


def test_household_id_must_be_non_empty():
    with pytest.raises(ValueError):
        CompositeObserver(providers=(), household_id="  ")
