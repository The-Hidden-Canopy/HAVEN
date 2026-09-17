"""fuse_presence / fuse_context: noisy-OR combination, agreement required."""

from datetime import datetime, timedelta, timezone

import pytest

from haven.core.domain import ContextState, EvidenceStatus, PresenceState
from haven.perception import SensorDisagreement, fuse_context, fuse_presence

BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


def test_noisy_or_combines_two_agreeing_sources_above_either_alone():
    camera = PresenceState(
        person_id="gerron", room_id="living_room", present=True, observed_at=BASE_TIME, source="vision.cam",
        confidence=0.7,
    )
    thermal = PresenceState(
        person_id="gerron", room_id="living_room", present=True, observed_at=BASE_TIME + timedelta(seconds=1),
        source="thermal.sensor", confidence=0.7,
    )

    fused = fuse_presence([camera, thermal], source="perception.fused")

    assert fused.present is True
    assert fused.status == EvidenceStatus.OBSERVED
    assert fused.confidence == pytest.approx(1 - (1 - 0.7) * (1 - 0.7))  # 0.91
    assert fused.observed_at == thermal.observed_at  # the most recent reading


def test_single_source_passes_through_unchanged_confidence():
    only = PresenceState(
        person_id="gerron", room_id="kitchen", present=True, observed_at=BASE_TIME, source="vision.cam",
        confidence=0.6,
    )

    fused = fuse_presence([only], source="perception.fused")

    assert fused.confidence == pytest.approx(0.6)


def test_disagreeing_sources_raise_instead_of_picking_a_winner():
    present = PresenceState(
        person_id="gerron", room_id="office", present=True, observed_at=BASE_TIME, source="vision.cam", confidence=0.9
    )
    absent = PresenceState(
        person_id="gerron", room_id="office", present=False, observed_at=BASE_TIME, source="thermal.sensor",
        confidence=0.9,
    )

    with pytest.raises(SensorDisagreement):
        fuse_presence([present, absent], source="perception.fused")


def test_mismatched_person_or_room_is_rejected():
    a = PresenceState(
        person_id="gerron", room_id="office", present=True, observed_at=BASE_TIME, source="vision.cam", confidence=0.9
    )
    b = PresenceState(
        person_id="guest", room_id="office", present=True, observed_at=BASE_TIME, source="thermal.sensor",
        confidence=0.9,
    )

    with pytest.raises(ValueError):
        fuse_presence([a, b], source="perception.fused")


def test_fuse_presence_requires_at_least_one_state():
    with pytest.raises(ValueError):
        fuse_presence([], source="perception.fused")


def test_fuse_context_combines_agreeing_sources():
    a = ContextState(context_id="away", active=True, observed_at=BASE_TIME, source="vision.cam", confidence=0.8)
    b = ContextState(
        context_id="away", active=True, observed_at=BASE_TIME + timedelta(seconds=1), source="motion.sensor",
        confidence=0.5,
    )

    fused = fuse_context([a, b], source="perception.fused")

    assert fused.active is True
    assert fused.confidence == pytest.approx(1 - (1 - 0.8) * (1 - 0.5))  # 0.9


def test_fuse_context_disagreement_raises():
    a = ContextState(context_id="away", active=True, observed_at=BASE_TIME, source="vision.cam", confidence=0.8)
    b = ContextState(context_id="away", active=False, observed_at=BASE_TIME, source="motion.sensor", confidence=0.8)

    with pytest.raises(SensorDisagreement):
        fuse_context([a, b], source="perception.fused")
