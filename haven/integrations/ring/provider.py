"""Observation-only Ring provider and deterministic local simulator."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from haven.core.domain import ContextState, EvidenceStatus
from haven.perception.observation import Observation

from .events import RingEvent


class RingEventSource(Protocol):
    """The only transport seam a future live Ring adapter must implement."""

    def read_events(self) -> tuple[RingEvent, ...]:
        """Return the currently observable bounded event batch."""


class RingSimulator:
    """In-memory source for offline proof and repeatable acceptance tests."""

    def __init__(self, events: tuple[RingEvent, ...] = ()) -> None:
        self._events: dict[str, RingEvent] = {}
        for event in events:
            self.push(event)

    def push(self, event: RingEvent) -> None:
        if not isinstance(event, RingEvent):
            raise ValueError("RingSimulator accepts RingEvent values only")
        if event.event_id in self._events:
            raise ValueError(f"duplicate Ring event: {event.event_id}")
        self._events[event.event_id] = event

    def read_events(self) -> tuple[RingEvent, ...]:
        return tuple(
            sorted(self._events.values(), key=lambda event: (event.observed_at, event.event_id))
        )


class RingEvidenceProvider:
    """Adapt Ring events to HAVEN's generic observation contract.

    Each event becomes an ephemeral context keyed by device and event kind.
    ``available=False`` is retained as explicit unavailable evidence; it is
    never converted into an inactive/clear reading that could be mistaken for
    a real negative observation.
    """

    provider_id = "haven.ring"

    def __init__(self, source: RingEventSource) -> None:
        self._source = source

    def events(self) -> tuple[RingEvent, ...]:
        return self._source.read_events()

    def observe(self) -> tuple[Observation, ...]:
        return tuple(self._to_context(event) for event in self.events())

    @staticmethod
    def _to_context(event: RingEvent) -> ContextState:
        return ContextState(
            context_id=f"ring:{event.device_id}:{event.kind.value}",
            active=event.available,
            observed_at=event.observed_at,
            source=event.source,
            status=EvidenceStatus.OBSERVED if event.available else EvidenceStatus.UNAVAILABLE,
            confidence=event.confidence,
        )


__all__ = ["RingEvidenceProvider", "RingEventSource", "RingSimulator"]
