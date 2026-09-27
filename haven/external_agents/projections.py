"""Allow-listed read projections for external agents.

The native/web application state is a presentation aggregate, not an export
contract.  This module intentionally knows only about mappings and JSON-safe
values so the external-agent boundary stays independent of ``haven.web``.
New fields added to the application's state are invisible here until they are
explicitly admitted to this projection.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


_ROOM_FIELDS = ("id", "name")
_DEVICE_FIELDS = (
    "id",
    "role",
    "is_on",
    "brightness_pct",
    "observed_at",
    "status",
    "confidence",
    "cover_state",
    "lock_state",
    "climate_mode",
    "current_temperature",
    "target_temperature",
    "camera_available",
    "motion_detected",
)
_PRESENCE_FIELDS = (
    "id",
    "person_id",
    "name",
    "room",
    "room_id",
    "present",
    "observed_at",
    "confidence",
    "status",
)
_CONTEXT_FIELDS = ("id", "context_id", "label", "active", "observed_at", "confidence", "status")
_CAMERA_FIELDS = ("id", "label", "motion", "online")


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _scalar(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _allowed_scalars(value: object, fields: tuple[str, ...]) -> dict[str, Any]:
    record = _mapping(value)
    if record is None:
        return {}
    result: dict[str, Any] = {}
    for field in fields:
        if field in record:
            scalar = _scalar(record[field])
            if scalar is not None or record[field] is None:
                result[field] = scalar
    return result


def _project_device(value: object) -> dict[str, Any]:
    return _allowed_scalars(value, _DEVICE_FIELDS)


def _project_camera(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    return _allowed_scalars(value, _CAMERA_FIELDS)


def _project_room(value: object) -> dict[str, Any]:
    record = _mapping(value)
    if record is None:
        return {}
    result = _allowed_scalars(record, _ROOM_FIELDS)
    if "devices" in record:
        result["devices"] = [_project_device(item) for item in _sequence(record["devices"])]
    if "people" in record:
        result["people"] = [item for item in _sequence(record["people"]) if _scalar(item) is not None]
    if "camera" in record:
        result["camera"] = _project_camera(record["camera"])
    return result


def _sequence(value: object) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _project_presence(value: object) -> dict[str, Any]:
    return _allowed_scalars(value, _PRESENCE_FIELDS)


def _project_context(value: object) -> dict[str, Any]:
    return _allowed_scalars(value, _CONTEXT_FIELDS)


def _flatten_devices(rooms: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    seen: set[str] = set()
    devices: list[dict[str, Any]] = []
    for room in rooms:
        for device in room.get("devices", []):
            device_id = device.get("id")
            if not isinstance(device_id, str) or device_id in seen:
                continue
            seen.add(device_id)
            devices.append(dict(device))
    return tuple(devices)


@dataclass(frozen=True, slots=True)
class WorldReadProjection:
    """The complete, scope-safe payload for ``world.read``."""

    revision: int | None
    observed_at: str | None
    rooms: tuple[dict[str, Any], ...]
    presence: tuple[dict[str, Any], ...]
    contexts: tuple[dict[str, Any], ...]
    devices: tuple[dict[str, Any], ...]
    freshness: Mapping[str, Any]

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "WorldReadProjection":
        rooms = tuple(_project_room(item) for item in _sequence(state.get("rooms", ())))
        people = state.get("presence", state.get("people", ()))
        presence = tuple(_project_presence(item) for item in _sequence(people))
        contexts = tuple(_project_context(item) for item in _sequence(state.get("contexts", ())))
        explicit_devices = tuple(
            _project_device(item) for item in _sequence(state.get("devices", ()))
        )
        devices = explicit_devices or _flatten_devices(rooms)
        revision = state.get("revision")
        return cls(
            revision=revision if isinstance(revision, int) else None,
            observed_at=(
                state.get("observed_at") if isinstance(state.get("observed_at"), str) else None
            ),
            rooms=rooms,
            presence=presence,
            contexts=contexts,
            devices=devices,
            freshness=dict(_mapping(state.get("freshness")) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-shaped value with no internal references."""

        return deepcopy({
            "revision": self.revision,
            "observed_at": self.observed_at,
            "rooms": [dict(room) for room in self.rooms],
            "presence": [dict(person) for person in self.presence],
            "contexts": [dict(context) for context in self.contexts],
            "devices": [dict(device) for device in self.devices],
            "freshness": dict(self.freshness),
        })


__all__ = ["WorldReadProjection"]
