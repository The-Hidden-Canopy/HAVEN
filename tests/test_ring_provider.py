from datetime import datetime, timezone

import pytest

from haven.core.domain import ContextState, EvidenceStatus
from haven.integrations.ring import RingEvent, RingEventKind, RingEvidenceProvider, RingSimulator
from haven.integrations.ring.plugin import PLUGIN


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _event(event_id: str, *, available: bool = True) -> RingEvent:
    return RingEvent(
        event_id=event_id,
        device_id="front-doorbell",
        kind=RingEventKind.DOORBELL,
        observed_at=NOW,
        available=available,
        confidence=0.9,
        metadata={"fixture": "true"},
    )


def test_ring_event_is_bounded_and_normalizes_metadata() -> None:
    event = _event("event-1")
    assert event.to_dict()["metadata"] == {"fixture": "true"}
    assert event.source == "ring.simulator"

    with pytest.raises(ValueError, match="metadata values"):
        RingEvent(
            event_id="bad",
            device_id="doorbell",
            kind=RingEventKind.MOTION,
            observed_at=NOW,
            metadata=(("raw", 1),),  # type: ignore[arg-type]
        )


def test_simulator_is_deterministic_and_rejects_duplicate_events() -> None:
    simulator = RingSimulator((_event("b"), _event("a")))
    assert [event.event_id for event in simulator.read_events()] == ["a", "b"]
    with pytest.raises(ValueError, match="duplicate"):
        simulator.push(_event("a"))


def test_provider_preserves_provenance_and_unavailable_state() -> None:
    provider = RingEvidenceProvider(RingSimulator((_event("live"), _event("down", available=False))))
    observations = provider.observe()
    assert all(isinstance(item, ContextState) for item in observations)
    assert all(item.source == "ring.simulator" for item in observations)
    by_status = {item.status: item for item in observations}
    assert by_status[EvidenceStatus.OBSERVED].active is True
    assert by_status[EvidenceStatus.UNAVAILABLE].active is False


def test_plugin_is_simulator_only_and_declares_read_only_operation() -> None:
    manifest = PLUGIN.describe()
    assert manifest.provider_id == "haven.ring.simulator"
    assert manifest.operational.observation is True
    assert manifest.operational.mutation is False
    assert tuple(item.value for item in manifest.operational.destructive_action_classes) == ("read_only",)
    assert isinstance(PLUGIN.build(config={}), RingEvidenceProvider)
    with pytest.raises(ValueError, match="simulator"):
        PLUGIN.build(config={"mode": "live"})
