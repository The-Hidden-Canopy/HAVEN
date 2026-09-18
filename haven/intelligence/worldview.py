"""WorldView: the bounded, serializable projection agents are allowed to see.

Architectural rule: intelligence providers never receive the store, the
snapshot internals, or device internals. `AgentContext` used to carry only
identity and conversation lines, which made "is the garage still open?"
unanswerable -- so a `WorldView` is the bounded answer: what was observed,
when, by whom, how confidently, and whether the evidence is still fresh.

A WorldView is plain frozen data: JSON round-trips exactly, it crosses
process boundaries (the http intelligence backend POSTs exactly this shape
to a remote agent), and it carries uncertainty verbatim -- a 0.7-confidence
presence is a 0.7, not a boolean. Freshness is derived, not stored truth:
`fresh` compares each observation against the snapshot's validity window
and the caller's `now`, the same semantics `WorldSnapshot` uses for
authority decisions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from haven.core.domain import DeviceState, DomainEvent, EventType, EvidenceStatus, WorldSnapshot
from haven.core.time import require_aware_utc

# The snapshot a deployment hands out is already bounded, but a projection
# that crosses a network boundary gets a hard ceiling anyway: past this many
# entries per collection the view is truncated (newest-first evidence wins)
# and `truncated` is set so no consumer mistakes the view for complete.
_MAX_ENTRIES_PER_COLLECTION = 200

# Recent transitions are a UI/agent convenience, not evidence: a short,
# newest-first tail of device/state-related events is plenty to answer
# "what just happened around here?".
_MAX_RECENT_TRANSITIONS = 10

# Event types that describe something happening to a device or its state.
_TRANSITION_EVENT_TYPES = frozenset(
    {
        EventType.ACTION_AUTHORIZED,
        EventType.ACTION_EXECUTED,
        EventType.ACTION_BLOCKED,
    }
)


def _iso(value: datetime) -> str:
    return require_aware_utc(value, name="timestamp").isoformat()


def _parse_iso(value: str, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO timestamp string")
    try:
        return require_aware_utc(datetime.fromisoformat(value), name=name)
    except ValueError as exc:
        raise ValueError(f"{name} is not a parseable ISO timestamp: {value!r}") from exc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_confidence(value: float, *, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a number between 0.0 and 1.0")
    return float(value)


def _truncate(items: tuple, cap: int) -> tuple[tuple, bool]:
    if len(items) <= cap:
        return items, False
    return items[:cap], True


@dataclass(frozen=True)
class WorldDeviceView:
    """One device's observable state, as evidence, not as device internals."""

    device_id: str
    room_id: str | None
    kind: str
    is_on: bool | None
    brightness_pct: int | None
    observed_at: str
    fresh: bool
    changed_by: str
    confidence: float
    name: str | None = None
    room_name: str | None = None
    capabilities: tuple[str, ...] = ()
    attributes: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _require_text(self.device_id, name="device_id"))
        if self.room_id is not None:
            object.__setattr__(self, "room_id", _require_text(self.room_id, name="room_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        if self.name is not None:
            object.__setattr__(self, "name", _require_text(self.name, name="name"))
        if self.room_name is not None:
            object.__setattr__(self, "room_name", _require_text(self.room_name, name="room_name"))
        if self.brightness_pct is not None and not 0 <= self.brightness_pct <= 100:
            raise ValueError("brightness_pct must be between 0 and 100")
        _parse_iso(self.observed_at, name="observed_at")
        object.__setattr__(self, "confidence", _require_confidence(self.confidence, name="confidence"))
        object.__setattr__(self, "changed_by", _require_text(self.changed_by, name="changed_by"))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(
            self,
            "attributes",
            tuple(sorted(((str(key), value) for key, value in self.attributes), key=lambda item: item[0])),
        )

    @property
    def uncertain(self) -> bool:
        """Cheap gating flag; the verbatim `confidence` stays the source of truth."""

        return self.confidence < 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "room_id": self.room_id,
            "kind": self.kind,
            "is_on": self.is_on,
            "brightness_pct": self.brightness_pct,
            "observed_at": self.observed_at,
            "fresh": self.fresh,
            "changed_by": self.changed_by,
            "confidence": self.confidence,
            "uncertain": self.uncertain,
            "name": self.name if self.name is not None else self.device_id,
            "room_name": self.room_name if self.room_name is not None else _room_name(self.room_id, names=None),
            "capabilities": list(self.capabilities),
            "attributes": {key: value for key, value in self.attributes},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldDeviceView":
        if not isinstance(data, dict):
            raise ValueError("device view must be a JSON object")
        attributes = data.get("attributes")
        if attributes is None:
            # Pre-enrichment payloads carried is_on/brightness_pct top-level;
            # flow them into the attributes bag so old reads keep their shape.
            attributes = {
                key: data[key] for key in ("is_on", "brightness_pct") if data.get(key) is not None
            }
        return cls(
            device_id=data["device_id"],
            room_id=data.get("room_id"),
            kind=data["kind"],
            is_on=data.get("is_on"),
            brightness_pct=data.get("brightness_pct"),
            observed_at=data["observed_at"],
            fresh=bool(data.get("fresh", False)),
            changed_by=data.get("changed_by", "system"),
            confidence=data.get("confidence", 1.0),
            name=data.get("name"),
            room_name=data.get("room_name"),
            capabilities=tuple(data.get("capabilities", ())),
            attributes=tuple(attributes.items()),
        )


@dataclass(frozen=True)
class WorldPresenceView:
    """One person's observed presence in one room, uncertainty included."""

    person_id: str
    room_id: str | None
    present: bool
    confidence: float
    observed_at: str
    fresh: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "person_id", _require_text(self.person_id, name="person_id"))
        if self.room_id is not None:
            object.__setattr__(self, "room_id", _require_text(self.room_id, name="room_id"))
        object.__setattr__(self, "confidence", _require_confidence(self.confidence, name="confidence"))
        _parse_iso(self.observed_at, name="observed_at")

    @property
    def uncertain(self) -> bool:
        return self.confidence < 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "room_id": self.room_id,
            "present": self.present,
            "confidence": self.confidence,
            "uncertain": self.uncertain,
            "observed_at": self.observed_at,
            "fresh": self.fresh,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldPresenceView":
        if not isinstance(data, dict):
            raise ValueError("presence view must be a JSON object")
        return cls(
            person_id=data["person_id"],
            room_id=data.get("room_id"),
            present=bool(data.get("present", False)),
            confidence=data.get("confidence", 1.0),
            observed_at=data["observed_at"],
            fresh=bool(data.get("fresh", False)),
        )


@dataclass(frozen=True)
class WorldContextView:
    """One household context's observed activation state."""

    context_id: str
    active: bool
    observed_at: str
    fresh: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _require_text(self.context_id, name="context_id"))
        _parse_iso(self.observed_at, name="observed_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "active": self.active,
            "observed_at": self.observed_at,
            "fresh": self.fresh,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldContextView":
        if not isinstance(data, dict):
            raise ValueError("context view must be a JSON object")
        return cls(
            context_id=data["context_id"],
            active=bool(data.get("active", False)),
            observed_at=data["observed_at"],
            fresh=bool(data.get("fresh", False)),
        )


@dataclass(frozen=True)
class WorldTransitionView:
    """One line of recent device/state history, rendered from a DomainEvent."""

    at: str
    device_id: str | None
    room_id: str | None
    summary: str

    def __post_init__(self) -> None:
        _parse_iso(self.at, name="transition at")
        if self.device_id is not None:
            object.__setattr__(self, "device_id", _require_text(self.device_id, name="device_id"))
        if self.room_id is not None:
            object.__setattr__(self, "room_id", _require_text(self.room_id, name="room_id"))
        object.__setattr__(self, "summary", _require_text(self.summary, name="summary"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "device_id": self.device_id,
            "room_id": self.room_id,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldTransitionView":
        if not isinstance(data, dict):
            raise ValueError("transition view must be a JSON object")
        return cls(
            at=data["at"],
            device_id=data.get("device_id"),
            room_id=data.get("room_id"),
            summary=data["summary"],
        )


def _transition_from_event(event: DomainEvent, *, device_registry: Any = None) -> WorldTransitionView:
    """Render one device/state DomainEvent as a single honest line.

    `device_id` comes from the event payload when present; `room_id` is
    resolved through the device registry when one is supplied. The summary
    states what happened and who did it -- never more than the event knows.
    """

    payload = dict(event.payload)
    device_id = payload.get("target_device_id")
    if not isinstance(device_id, str) or not device_id:
        device_id = None
    room_id = None
    if device_id is not None and device_registry is not None and device_registry.is_registered(device_id):
        room_id = device_registry.get(device_id).room
    return WorldTransitionView(
        at=_iso(event.occurred_at),
        device_id=device_id,
        room_id=room_id,
        summary=f"{event.event_type.value} by {event.actor_id}",
    )


def _device_name(device_id: str, state: DeviceState, *, names: Any) -> str:
    """Readable device label: the caller's mapping, else "role · id tail"."""

    if names is not None:
        mapped = names.get(device_id)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    return f"{state.kind} · {device_id}"


def _room_name(room_id: str | None, *, names: Any) -> str | None:
    if room_id is None:
        return None
    if names is not None:
        mapped = names.get(room_id)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    return room_id.replace("_", " ").title()


@dataclass(frozen=True)
class WorldView:
    """The whole bounded world an agent may reason about.

    `captured_at`/`valid_until` are the snapshot's validity window as ISO
    strings; `truncated` records when the hard per-collection ceiling cut
    evidence, so a consumer never mistakes a partial view for the house.
    """

    household_id: str
    captured_at: str
    valid_until: str
    devices: tuple[WorldDeviceView, ...] = ()
    presence: tuple[WorldPresenceView, ...] = ()
    contexts: tuple[WorldContextView, ...] = ()
    recent_transitions: tuple[WorldTransitionView, ...] = ()
    truncated: bool = field(default=False, compare=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "household_id", _require_text(self.household_id, name="household_id"))
        captured_at = _parse_iso(self.captured_at, name="captured_at")
        valid_until = _parse_iso(self.valid_until, name="valid_until")
        if valid_until <= captured_at:
            raise ValueError("valid_until must be later than captured_at")
        for collection_name, item_type in (
            ("devices", WorldDeviceView),
            ("presence", WorldPresenceView),
            ("contexts", WorldContextView),
            ("recent_transitions", WorldTransitionView),
        ):
            collection = tuple(getattr(self, collection_name))
            for item in collection:
                if not isinstance(item, item_type):
                    raise ValueError(f"{collection_name} contains an invalid view value")
            object.__setattr__(self, collection_name, collection)

    @property
    def transition_count(self) -> int:
        return len(self.recent_transitions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "household_id": self.household_id,
            "captured_at": self.captured_at,
            "valid_until": self.valid_until,
            "devices": [item.to_dict() for item in self.devices],
            "presence": [item.to_dict() for item in self.presence],
            "contexts": [item.to_dict() for item in self.contexts],
            "recent_transitions": [item.to_dict() for item in self.recent_transitions],
            "transition_count": self.transition_count,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldView":
        if not isinstance(data, dict):
            raise ValueError("world view must be a JSON object")
        return cls(
            household_id=data["household_id"],
            captured_at=data["captured_at"],
            valid_until=data["valid_until"],
            devices=tuple(WorldDeviceView.from_dict(item) for item in data.get("devices", ())),
            presence=tuple(WorldPresenceView.from_dict(item) for item in data.get("presence", ())),
            contexts=tuple(WorldContextView.from_dict(item) for item in data.get("contexts", ())),
            recent_transitions=tuple(
                WorldTransitionView.from_dict(item) for item in data.get("recent_transitions", ())
            ),
            truncated=bool(data.get("truncated", False)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "WorldView":
        if not isinstance(payload, str):
            raise ValueError("world view JSON must be a string")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"world view payload is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: WorldSnapshot,
        *,
        now: datetime,
        device_registry: Any = None,
        names: Any = None,
        recent_events: Any = None,
    ) -> "WorldView":
        """Project a snapshot into the bounded agent-facing view.

        Freshness uses exactly the snapshot's own semantics: an observation
        is fresh when it is OBSERVED-status, was observed no later than
        `now`, and `now` is inside the snapshot's validity window. Confidence
        and `changed_by` are carried verbatim -- the view hides nothing about
        how much the evidence can be trusted.

        `device_registry` and `names` are optional enrichment: with a
        registry each device view carries its manifest capability names; with
        a names mapping (device ids AND room ids as keys) each device/room
        carries a readable label. Without them the projection still carries
        every id, so nothing becomes unaddressable. `recent_events` supplies
        DomainEvents rendered into `recent_transitions` (newest first, hard
        cap `_MAX_RECENT_TRANSITIONS`, device/state-related only); absent
        means no history, not invented history.
        """

        if not isinstance(snapshot, WorldSnapshot):
            raise ValueError("from_snapshot expects a WorldSnapshot")
        now = require_aware_utc(now, name="projection time")
        valid = snapshot.captured_at <= now <= snapshot.valid_until

        def fresh(state: DeviceState) -> bool:
            return (
                valid
                and state.status is EvidenceStatus.OBSERVED
                and state.observed_at <= now
            )

        def attributes(state: DeviceState) -> tuple[tuple[str, Any], ...]:
            pairs: list[tuple[str, Any]] = []
            if state.is_on is not None:
                pairs.append(("is_on", state.is_on))
            if state.brightness_pct is not None:
                pairs.append(("brightness_pct", state.brightness_pct))
            # Each device kind's own vocabulary, alongside the boolean pair
            # above -- a cover's real open/closed/opening/closing (etc.)
            # never has to be reconstructed by an agent from `is_on` alone.
            if state.raw_state is not None:
                pairs.append(("raw_state", state.raw_state))
            if state.cover_state is not None:
                pairs.append(("cover_state", state.cover_state.value))
            if state.lock_state is not None:
                pairs.append(("lock_state", state.lock_state.value))
            if state.climate_mode is not None:
                pairs.append(("climate_mode", state.climate_mode))
            if state.current_temperature is not None:
                pairs.append(("current_temperature", state.current_temperature))
            if state.target_temperature is not None:
                pairs.append(("target_temperature", state.target_temperature))
            if state.camera_available is not None:
                pairs.append(("camera_available", state.camera_available))
            if state.motion_detected is not None:
                pairs.append(("motion_detected", state.motion_detected))
            return tuple(pairs)

        def capabilities(device_id: str) -> tuple[str, ...]:
            if device_registry is None or not device_registry.is_registered(device_id):
                return ()
            return device_registry.get(device_id).capability_names

        devices, cut_devices = _truncate(snapshot.devices, _MAX_ENTRIES_PER_COLLECTION)
        presence, cut_presence = _truncate(snapshot.presence, _MAX_ENTRIES_PER_COLLECTION)
        contexts, cut_contexts = _truncate(snapshot.contexts, _MAX_ENTRIES_PER_COLLECTION)

        transitions: list[WorldTransitionView] = []
        cut_transitions = False
        if recent_events is not None:
            related = [
                event
                for event in recent_events
                if isinstance(event, DomainEvent) and event.event_type in _TRANSITION_EVENT_TYPES
            ]
            related.sort(key=lambda event: event.occurred_at, reverse=True)
            bounded, cut_transitions = _truncate(tuple(related), _MAX_RECENT_TRANSITIONS)
            transitions = [
                _transition_from_event(event, device_registry=device_registry) for event in bounded
            ]
        return cls(
            household_id=snapshot.household_id,
            captured_at=_iso(snapshot.captured_at),
            valid_until=_iso(snapshot.valid_until),
            devices=tuple(
                WorldDeviceView(
                    device_id=state.device_id,
                    room_id=state.room_id,
                    kind=state.kind,
                    is_on=state.is_on,
                    brightness_pct=state.brightness_pct,
                    observed_at=_iso(state.observed_at),
                    fresh=fresh(state),
                    changed_by=state.changed_by.value,
                    confidence=state.confidence,
                    name=_device_name(state.device_id, state, names=names),
                    room_name=_room_name(state.room_id, names=names),
                    capabilities=capabilities(state.device_id),
                    attributes=attributes(state),
                )
                for state in devices
            ),
            presence=tuple(
                WorldPresenceView(
                    person_id=state.person_id,
                    room_id=state.room_id,
                    present=state.present,
                    confidence=state.confidence,
                    observed_at=_iso(state.observed_at),
                    fresh=fresh(state),
                )
                for state in presence
            ),
            contexts=tuple(
                WorldContextView(
                    context_id=state.context_id,
                    active=state.active,
                    observed_at=_iso(state.observed_at),
                    fresh=fresh(state),
                )
                for state in contexts
            ),
            recent_transitions=tuple(transitions),
            truncated=cut_devices or cut_presence or cut_contexts or cut_transitions,
        )


__all__ = [
    "WorldContextView",
    "WorldDeviceView",
    "WorldPresenceView",
    "WorldTransitionView",
    "WorldView",
]
