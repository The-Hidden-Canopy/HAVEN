"""CompositeObserver: turn N `ObservationProvider`s into one `WorldSnapshot`.

The provider-agnostic counterpart to `HomeAssistantObserver`
(`haven/integrations/home_assistant/observer.py`): any number of installed
providers -- built-in or a community package loaded through
`haven.providers.loader` -- each report their own flat batch of
`Observation`s (`haven/perception/observation.py`). This assembles them into
the one `WorldSnapshot` shape Haven Core consumes, fusing same-fact
presence/context readings the same way multiple sensors covering one room
already combine (`haven.perception.fusion`, noisy-OR). A device has exactly
one authoritative reading per provider today, so device states are not
fused across sources -- the most recently observed one wins, the same way a
single Home Assistant fetch already reports one state per entity.

This is meant to be wrapped by `HomeAssistantWorldProvider`
(`haven/integrations/home_assistant/world.py`, generic over any object
satisfying `observe(now=...)` despite its name) for the same
cached/degrade-to-fallback behavior a Home Assistant-backed household
already gets -- a household composed from community providers is not a
second-class citizen with its own bespoke "no evidence" representation.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable
from uuid import uuid4

from haven.core.domain import ContextState, DeviceState, EvidenceStatus, PresenceState, WorldSnapshot
from haven.perception.fusion import SensorDisagreement, fuse_context, fuse_presence
from haven.perception.observation import ObservationProvider

DEFAULT_SNAPSHOT_TTL = timedelta(minutes=2)
_FUSED_SOURCE = "composite.fused"


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class CompositeObserver:
    """One-shot `observe()`: merge every configured `ObservationProvider`."""

    def __init__(
        self,
        *,
        providers: Iterable[ObservationProvider],
        household_id: str,
        snapshot_ttl: timedelta = DEFAULT_SNAPSHOT_TTL,
    ) -> None:
        if snapshot_ttl <= timedelta(0):
            raise ValueError("snapshot_ttl must be positive")
        self._providers = tuple(providers)
        self._household_id = _require_text(household_id, name="household_id")
        self._snapshot_ttl = snapshot_ttl

    def observe(self, *, now: datetime) -> WorldSnapshot:
        presence: list[PresenceState] = []
        contexts: list[ContextState] = []
        devices: dict[str, DeviceState] = {}
        for provider in self._providers:
            for observation in provider.observe():
                if isinstance(observation, PresenceState):
                    presence.append(observation)
                elif isinstance(observation, ContextState):
                    contexts.append(observation)
                elif isinstance(observation, DeviceState):
                    existing = devices.get(observation.device_id)
                    if existing is None or observation.observed_at >= existing.observed_at:
                        devices[observation.device_id] = observation

        return WorldSnapshot(
            snapshot_id=f"snapshot-{uuid4().hex}",
            household_id=self._household_id,
            captured_at=now,
            valid_until=now + self._snapshot_ttl,
            presence=_fuse_presence_groups(presence),
            contexts=_fuse_context_groups(contexts),
            devices=tuple(devices.values()),
        )


def _fuse_presence_groups(states: Iterable[PresenceState]) -> tuple[PresenceState, ...]:
    groups: dict[tuple[str, str], list[PresenceState]] = defaultdict(list)
    for state in states:
        if state.status == EvidenceStatus.OBSERVED:
            groups[(state.person_id, state.room_id)].append(state)
    fused: list[PresenceState] = []
    for group in groups.values():
        try:
            fused.append(fuse_presence(group, source=_FUSED_SOURCE))
        except SensorDisagreement:
            # Two sources disagree on the fact itself: dropped from this
            # snapshot rather than guessed at, the same as any other
            # untrusted evidence -- absence of a reading is what a caller
            # (AuthorityEngine) already treats as fail-closed.
            continue
    return tuple(fused)


def _fuse_context_groups(states: Iterable[ContextState]) -> tuple[ContextState, ...]:
    groups: dict[str, list[ContextState]] = defaultdict(list)
    for state in states:
        if state.status == EvidenceStatus.OBSERVED:
            groups[state.context_id].append(state)
    fused: list[ContextState] = []
    for group in groups.values():
        try:
            fused.append(fuse_context(group, source=_FUSED_SOURCE))
        except SensorDisagreement:
            continue
    return tuple(fused)


__all__ = ["CompositeObserver", "DEFAULT_SNAPSHOT_TTL"]
