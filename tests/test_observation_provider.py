"""ObservationProvider / FixtureObservationProvider: the one shape any
perception source (BLE, WiFi, motion, a camera) implements."""

from datetime import datetime, timezone

from haven.core.domain import ContextState, PresenceState
from haven.perception import FixtureObservationProvider

BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


def test_returns_whatever_observations_it_was_given():
    presence = PresenceState(
        person_id="gerron", room_id="bedroom", present=True, observed_at=BASE_TIME, source="ble.proximity",
        confidence=0.72,
    )
    context = ContextState(context_id="working_late", active=True, observed_at=BASE_TIME, source="wifi.mdns")
    provider = FixtureObservationProvider((presence, context))

    observed = provider.observe()

    assert observed == (presence, context)


def test_empty_provider_observes_nothing():
    assert FixtureObservationProvider().observe() == ()
