"""Combine independent observations of the same fact into one.

A camera's person-detection confidence and a thermal sensor's warm-body
confidence are two independent readings of the same underlying fact (is
someone in this room). Neither `WorldSnapshot` nor `AuthorityEngine` assume
there is only ever one sensor per room; this module is the one place that
fuses several readings into the single `PresenceState`/`ContextState` those
engines actually consume.

There is no real vision, thermal, or IR provider in this repo, so this
operates on Haven's own typed evidence (`PresenceState`, `ContextState`),
which any real provider is expected to have already produced from its own
raw output -- the same boundary `device_states_from_ha()` draws for Home
Assistant's state format.
"""

from __future__ import annotations

from typing import Iterable

from haven.core.domain import ContextState, EvidenceStatus, PresenceState


class SensorDisagreement(ValueError):
    """Raised when fused sources disagree on the fact itself.

    Silently picking a side when two sensors disagree (one says present,
    one says absent) would be exactly the kind of guess Haven avoids
    elsewhere -- an unrecognized state is dropped rather than interpreted,
    and a low-confidence reading is rejected rather than accepted at a
    discount. Disagreement is surfaced here for a caller (a household, a
    future correlation policy) to resolve deliberately, not papered over
    with a majority vote or highest-confidence-wins rule this module would
    otherwise have to invent.
    """


def _noisy_or(confidences: Iterable[float]) -> float:
    """The standard way to combine independent evidence for the same claim.

    Two sensors each 70% confident combine to 1 - (1-0.7)(1-0.7) = 0.91, not
    diluted to 70% (averaging) or capped at the max (either alone).
    """

    combined_absence = 1.0
    for confidence in confidences:
        combined_absence *= 1.0 - confidence
    return 1.0 - combined_absence


def fuse_presence(states: Iterable[PresenceState], *, source: str) -> PresenceState:
    """Combine independent PresenceState readings of one person+room into one.

    Every state must already agree on `person_id`, `room_id`, and `present`;
    a disagreement raises `SensorDisagreement` rather than being resolved
    here. The result carries the noisy-OR-combined confidence, the most
    recent `observed_at` among the sources, and `EvidenceStatus.OBSERVED`
    regardless of the individual sources' statuses -- a caller fusing
    UNAVAILABLE or STALE readings into this should filter those out first,
    the same way it would for any other evidence it does not trust.
    """

    states = tuple(states)
    if not states:
        raise ValueError("fuse_presence requires at least one PresenceState")
    first = states[0]
    for state in states[1:]:
        if state.person_id != first.person_id or state.room_id != first.room_id:
            raise ValueError("fuse_presence requires every state to describe the same person_id and room_id")
        if state.present != first.present:
            raise SensorDisagreement(
                f"sources disagree on presence for {first.person_id}@{first.room_id}: "
                + ", ".join(f"{s.source}={s.present}@{s.confidence}" for s in states)
            )
    latest = max(states, key=lambda s: s.observed_at)
    return PresenceState(
        person_id=first.person_id,
        room_id=first.room_id,
        present=first.present,
        observed_at=latest.observed_at,
        source=source,
        status=EvidenceStatus.OBSERVED,
        confidence=_noisy_or(s.confidence for s in states),
    )


def fuse_context(states: Iterable[ContextState], *, source: str) -> ContextState:
    """The `ContextState` counterpart to `fuse_presence` -- same contract."""

    states = tuple(states)
    if not states:
        raise ValueError("fuse_context requires at least one ContextState")
    first = states[0]
    for state in states[1:]:
        if state.context_id != first.context_id:
            raise ValueError("fuse_context requires every state to describe the same context_id")
        if state.active != first.active:
            raise SensorDisagreement(
                f"sources disagree on context {first.context_id!r}: "
                + ", ".join(f"{s.source}={s.active}@{s.confidence}" for s in states)
            )
    latest = max(states, key=lambda s: s.observed_at)
    return ContextState(
        context_id=first.context_id,
        active=first.active,
        observed_at=latest.observed_at,
        source=source,
        status=EvidenceStatus.OBSERVED,
        confidence=_noisy_or(s.confidence for s in states),
    )


__all__ = ["SensorDisagreement", "fuse_context", "fuse_presence"]
