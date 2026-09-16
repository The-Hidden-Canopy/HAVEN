"""Assemble Haven WorldSnapshot evidence from a live Home Assistant.

`LiveHomeAssistantAdapter` moves commands and raw state in both directions;
this observer is what turns a single fetch into the immutable
`WorldSnapshot` the rest of Haven consumes. It is deliberately single-shot:
no polling loop lives here. A deployment calls `observe()` on whatever
cadence it wants -- Haven Core still has no scheduler.

Presence and context meaning is household-specific, so it is declared, not
inferred: a deployment says "binary_sensor.gerron_bedroom_occupancy tells me
whether gerron is in the bedroom" by registering a `PresenceSource`, and
Haven never guesses which entities carry which meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Protocol
from uuid import uuid4

from haven.core.domain import ContextState, DeviceState, EvidenceStatus, PresenceState, WorldSnapshot
from haven.devices import DeviceRegistry

from .state import device_states_from_ha


_OBSERVED_SOURCE = "home_assistant.rest"
DEFAULT_SNAPSHOT_TTL = timedelta(minutes=2)

_PRESENT_STATES = frozenset({"home", "on"})
_ABSENT_STATES = frozenset({"not_home", "off"})
_INACTIVE_STATES = frozenset({"unavailable", "unknown"})


class HomeAssistantStateSource(Protocol):
    def fetch_states(self) -> tuple[dict, ...]:
        """Return Home Assistant's /api/states list."""


@dataclass(frozen=True)
class PresenceSource:
    """Declares that one HA entity reports whether a person is in a room."""

    entity_id: str
    person_id: str
    room_id: str


@dataclass(frozen=True)
class ContextSource:
    """Declares that one HA entity reports whether a household context is active."""

    entity_id: str
    context_id: str


def _observed_at(state: Mapping[str, Any]) -> datetime | None:
    value = state.get("last_changed")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _by_entity(states: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {state["entity_id"]: state for state in states if isinstance(state.get("entity_id"), str)}


class HomeAssistantObserver:
    """One-shot observe: fetch HA state and assemble a WorldSnapshot."""

    def __init__(
        self,
        *,
        client: HomeAssistantStateSource,
        device_registry: DeviceRegistry,
        household_id: str,
        presence_sources: Iterable[PresenceSource] = (),
        context_sources: Iterable[ContextSource] = (),
        snapshot_ttl: timedelta = DEFAULT_SNAPSHOT_TTL,
    ) -> None:
        if snapshot_ttl <= timedelta(0):
            raise ValueError("snapshot_ttl must be positive")
        self._client = client
        self._device_registry = device_registry
        self._household_id = household_id
        self._presence_sources = tuple(presence_sources)
        self._context_sources = tuple(context_sources)
        self._snapshot_ttl = snapshot_ttl

    def observe(self, *, now: datetime) -> WorldSnapshot:
        """Fetch current HA state and return it as one WorldSnapshot.

        A fetch failure raises `HomeAssistantStateError` from the client: an
        unobserved household is exceptional, not an empty snapshot, and Haven
        never fabricates evidence for devices Home Assistant did not report.
        """

        states = self._client.fetch_states()
        by_entity = _by_entity(states)
        return WorldSnapshot(
            snapshot_id=f"snapshot-{uuid4().hex}",
            household_id=self._household_id,
            captured_at=now,
            valid_until=now + self._snapshot_ttl,
            presence=self._presence(by_entity),
            contexts=self._contexts(by_entity),
            devices=device_states_from_ha(states, self._device_registry),
        )

    def _presence(self, by_entity: Mapping[str, Mapping[str, Any]]) -> tuple[PresenceState, ...]:
        observed: list[PresenceState] = []
        for source in self._presence_sources:
            state = by_entity.get(source.entity_id)
            if state is None:
                continue
            observed_at = _observed_at(state)
            if observed_at is None:
                continue
            value = state.get("state")
            if value in _PRESENT_STATES:
                present, status = True, EvidenceStatus.OBSERVED
            elif value in _ABSENT_STATES:
                present, status = False, EvidenceStatus.OBSERVED
            elif value in _INACTIVE_STATES:
                present, status = False, EvidenceStatus.UNAVAILABLE
            else:
                # A custom zone name or anything else this deployment has not
                # taught Haven to interpret -- skipped, not guessed at.
                continue
            observed.append(
                PresenceState(
                    person_id=source.person_id,
                    room_id=source.room_id,
                    present=present,
                    observed_at=observed_at,
                    source=_OBSERVED_SOURCE,
                    status=status,
                )
            )
        return tuple(observed)

    def _contexts(self, by_entity: Mapping[str, Mapping[str, Any]]) -> tuple[ContextState, ...]:
        observed: list[ContextState] = []
        for source in self._context_sources:
            state = by_entity.get(source.entity_id)
            if state is None:
                continue
            observed_at = _observed_at(state)
            if observed_at is None:
                continue
            value = state.get("state")
            if value == "on":
                active, status = True, EvidenceStatus.OBSERVED
            elif value == "off":
                active, status = False, EvidenceStatus.OBSERVED
            elif value in _INACTIVE_STATES:
                active, status = False, EvidenceStatus.UNAVAILABLE
            else:
                continue
            observed.append(
                ContextState(
                    context_id=source.context_id,
                    active=active,
                    observed_at=observed_at,
                    source=_OBSERVED_SOURCE,
                    status=status,
                )
            )
        return tuple(observed)


__all__ = [
    "ContextSource",
    "DEFAULT_SNAPSHOT_TTL",
    "HomeAssistantObserver",
    "HomeAssistantStateSource",
    "PresenceSource",
]
