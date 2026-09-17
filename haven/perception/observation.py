"""The ObservationProvider contract: any perception source, one shape.

BLE proximity, WiFi association, motion, door sensors, and camera/thermal
detections are all, at the boundary Haven cares about, just readings that
become `PresenceState`, `ContextState`, or `DeviceState`. `ObservationProvider`
is the one shape any of them implements; a caller collects `observe()` from
however many are configured and, where more than one describes the same
fact, combines them with `haven.perception.fuse_presence`/`fuse_context`
before folding the result into a `WorldSnapshot`.

This is deliberately a flat batch, not `HomeAssistantObserver`'s
`observe(now=...) -> WorldSnapshot`: a raw perception source does not know a
household's device registry or snapshot validity window, only what it just
observed.
"""

from __future__ import annotations

from typing import Protocol, Union

from haven.core.domain import ContextState, DeviceState, PresenceState

Observation = Union[PresenceState, ContextState, DeviceState]


class ObservationProvider(Protocol):
    def observe(self) -> tuple[Observation, ...]:
        """Return whatever this source currently observes."""


class FixtureObservationProvider:
    """Deterministic local provider used by tests and demonstrations."""

    def __init__(self, observations: tuple[Observation, ...] = ()) -> None:
        self._observations = tuple(observations)

    def observe(self) -> tuple[Observation, ...]:
        return self._observations


__all__ = ["FixtureObservationProvider", "Observation", "ObservationProvider"]
