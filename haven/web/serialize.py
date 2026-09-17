"""JSON serializers for the local web surface.

Serialization follows the ``ActionReceipt.to_dict()`` precedent: enums
serialize to their value, datetimes to ISO strings, and ``RoleTier`` to its
member name. Collections come out as lists, never tuples.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from haven.core.domain import DeviceState, DomainEvent, EventType, MemoryEntry, RoleTier


def to_json_value(value: Any) -> Any:
    if isinstance(value, RoleTier):
        return value.name
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    return value


def device_to_dict(device: DeviceState, *, role: str) -> dict[str, Any]:
    return {
        "id": device.device_id,
        "role": role,
        "is_on": device.is_on,
        "brightness_pct": device.brightness_pct,
        "observed_at": device.observed_at.isoformat(),
    }


def room_to_dict(
    *,
    room_id: str,
    name: str,
    devices: list[dict[str, Any]],
    people: list[str],
    camera: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": room_id,
        "name": name,
        "devices": list(devices),
        "people": list(people),
        "camera": camera,
    }


def camera_to_dict(*, camera_id: str, label: str, motion: bool) -> dict[str, Any]:
    return {"id": camera_id, "label": label, "motion": motion}


def person_to_dict(*, person_id: str, name: str, room: str) -> dict[str, Any]:
    return {"id": person_id, "name": name, "room": room}


def context_to_dict(*, context_id: str, label: str, active: bool) -> dict[str, Any]:
    return {"id": context_id, "label": label, "active": active}


def pending_to_dict(pending: Any) -> dict[str, Any]:
    return {
        "request_id": pending.request_id,
        "rule_id": pending.rule_id,
        "title": pending.title,
        "detail": pending.detail,
        "expires_at": pending.expires_at.isoformat(),
    }


def message_to_dict(*, sender: str, text: str) -> dict[str, Any]:
    return {"from": sender, "text": text}


def status_to_dict(*, devices: int, people: int, line: str, core: str = "Local Core") -> dict[str, Any]:
    return {"core": core, "devices": devices, "people": people, "line": line}


def event_to_dict(
    event: DomainEvent,
    *,
    rule_label: str | None = None,
    action_device: str | None = None,
) -> dict[str, Any]:
    """Render one domain event as a one-line activity row.

    ``rule_label`` and ``action_device`` let the caller resolve opaque ids to
    household-meaningful names (draft source text, device ids); without them
    the summary falls back to the raw payload value.
    """

    payload = dict(event.payload)
    rule_id = payload.get("rule_id")
    label = rule_label if rule_label is not None else (str(rule_id) if rule_id is not None else "unknown rule")
    device = payload.get("target_device_id") or action_device
    event_type = event.event_type
    if event_type == EventType.RULE_PROPOSED:
        summary = f"Rule proposed: {label}"
    elif event_type == EventType.RULE_APPROVED:
        summary = f"Rule approved: {label} by {event.actor_id}"
    elif event_type == EventType.RULE_CLARIFIED:
        summary = f"Rule clarified: {label} by {event.actor_id}"
    elif event_type in (EventType.RULE_APPROVAL_BLOCKED, EventType.RULE_CLARIFICATION_BLOCKED):
        summary = f"Rule blocked: {label} ({payload.get('code')})"
    elif event_type == EventType.ACTION_AUTHORIZED:
        summary = f"Action authorized on {device} by {event.actor_id}"
    elif event_type == EventType.ACTION_EXECUTED:
        summary = f"Action executed on {device} (success={payload.get('success')})"
    elif event_type == EventType.ACTION_BLOCKED:
        summary = f"Action blocked on {device} ({payload.get('status')})"
    else:
        summary = event_type.value
    return {
        "event_id": event.event_id,
        "event_type": event_type.value,
        "actor_id": event.actor_id,
        "occurred_at": event.occurred_at.isoformat(),
        "summary": summary,
    }


def memory_to_dict(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "kind": entry.kind,
        "content": entry.content,
        "recorded_at": entry.recorded_at.isoformat(),
    }


def state_to_dict(
    *,
    glow: str,
    revision: int,
    rooms: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    people: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    conversation: list[dict[str, Any]],
    status: dict[str, Any],
    glow_target: str | None = None,
    activity: list[dict[str, Any]] | None = None,
    memory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "glow": glow,
        "glow_target": glow_target,
        "revision": int(revision),
        "rooms": [to_json_value(room) for room in rooms],
        "contexts": [to_json_value(context) for context in contexts],
        "people": [to_json_value(person) for person in people],
        "pending": [to_json_value(item) for item in pending],
        "conversation": [to_json_value(message) for message in conversation],
        "activity": [to_json_value(item) for item in (activity or [])],
        "memory": [to_json_value(item) for item in (memory or [])],
        "status": to_json_value(status),
    }


__all__ = [
    "camera_to_dict",
    "context_to_dict",
    "device_to_dict",
    "event_to_dict",
    "memory_to_dict",
    "message_to_dict",
    "pending_to_dict",
    "person_to_dict",
    "room_to_dict",
    "state_to_dict",
    "status_to_dict",
    "to_json_value",
]
