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

from haven.core.domain import DeviceState, EvidenceStatus, WorldSnapshot
from haven.core.time import require_aware_utc

# The snapshot a deployment hands out is already bounded, but a projection
# that crosses a network boundary gets a hard ceiling anyway: past this many
# entries per collection the view is truncated (newest-first evidence wins)
# and `truncated` is set so no consumer mistakes the view for complete.
_MAX_ENTRIES_PER_COLLECTION = 200


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _require_text(self.device_id, name="device_id"))
        if self.room_id is not None:
            object.__setattr__(self, "room_id", _require_text(self.room_id, name="room_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        if self.brightness_pct is not None and not 0 <= self.brightness_pct <= 100:
            raise ValueError("brightness_pct must be between 0 and 100")
        _parse_iso(self.observed_at, name="observed_at")
        object.__setattr__(self, "confidence", _require_confidence(self.confidence, name="confidence"))
        object.__setattr__(self, "changed_by", _require_text(self.changed_by, name="changed_by"))

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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldDeviceView":
        if not isinstance(data, dict):
            raise ValueError("device view must be a JSON object")
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "room_id": self.room_id,
            "present": self.present,
            "confidence": self.confidence,
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
        ):
            collection = tuple(getattr(self, collection_name))
            for item in collection:
                if not isinstance(item, item_type):
                    raise ValueError(f"{collection_name} contains an invalid view value")
            object.__setattr__(self, collection_name, collection)

    def to_dict(self) -> dict[str, Any]:
        return {
            "household_id": self.household_id,
            "captured_at": self.captured_at,
            "valid_until": self.valid_until,
            "devices": [item.to_dict() for item in self.devices],
            "presence": [item.to_dict() for item in self.presence],
            "contexts": [item.to_dict() for item in self.contexts],
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
    def from_snapshot(cls, snapshot: WorldSnapshot, *, now: datetime) -> "WorldView":
        """Project a snapshot into the bounded agent-facing view.

        Freshness uses exactly the snapshot's own semantics: an observation
        is fresh when it is OBSERVED-status, was observed no later than
        `now`, and `now` is inside the snapshot's validity window. Confidence
        and `changed_by` are carried verbatim -- the view hides nothing about
        how much the evidence can be trusted.
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

        devices, cut_devices = _truncate(snapshot.devices, _MAX_ENTRIES_PER_COLLECTION)
        presence, cut_presence = _truncate(snapshot.presence, _MAX_ENTRIES_PER_COLLECTION)
        contexts, cut_contexts = _truncate(snapshot.contexts, _MAX_ENTRIES_PER_COLLECTION)
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
            truncated=cut_devices or cut_presence or cut_contexts,
        )


__all__ = [
    "WorldContextView",
    "WorldDeviceView",
    "WorldPresenceView",
    "WorldView",
]
