"""JSON serializers for the local web surface.

Serialization follows the ``ActionReceipt.to_dict()`` precedent: enums
serialize to their value, datetimes to ISO strings, and ``RoleTier`` to its
member name. Collections come out as lists, never tuples.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from haven.authority.policy import DEFAULT_MINIMUM_CONFIDENCE, HUMAN_OVERRIDE_WINDOW
from haven.core.domain import DeviceState, DomainEvent, EventType, MemoryEntry, RoleTier, Rule, RuleDraft
from haven.providers import ProviderCapabilities
from haven.scheduler import ScheduleStatus

SUMMARY_LIMIT = 90


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
        "status": device.status.value,
        "changed_by": device.changed_by.value,
        "confidence": device.confidence,
        "source": device.source,
        # The provider's literal state, always -- and each device kind's own
        # vocabulary alongside the boolean fields, never squeezed into them.
        "raw_state": device.raw_state,
        "cover_state": device.cover_state.value if device.cover_state is not None else None,
        "lock_state": device.lock_state.value if device.lock_state is not None else None,
        "climate_mode": device.climate_mode,
        "current_temperature": device.current_temperature,
        "target_temperature": device.target_temperature,
        "camera_available": device.camera_available,
        "motion_detected": device.motion_detected,
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


def camera_to_dict(*, camera_id: str, label: str, motion: bool, online: bool) -> dict[str, Any]:
    return {"id": camera_id, "label": label, "motion": motion, "online": online}


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


def voice_to_dict(*, state: str, mic: bool) -> dict[str, Any]:
    return {"state": state, "mic": mic}


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
    elif event_type == EventType.RULE_REVOKED:
        summary = f"Rule revoked: {label} by {event.actor_id}"
    elif event_type == EventType.RULE_CLARIFIED:
        summary = f"Rule clarified: {label} by {event.actor_id}"
    elif event_type in (
        EventType.RULE_APPROVAL_BLOCKED,
        EventType.RULE_CLARIFICATION_BLOCKED,
        EventType.RULE_REVOCATION_BLOCKED,
    ):
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


def _one_line(text: str, *, limit: int = SUMMARY_LIMIT) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _rule_target(draft: RuleDraft, *, device_room: str | None) -> str:
    if draft.target_device_id is not None:
        if device_room:
            room = device_room.replace("_", " ").title()
            return f"{draft.target_device_id} · {room}"
        return draft.target_device_id
    selector = draft.target_selector
    if selector is None:
        return "unresolved target"
    parts = [
        value
        for value in (selector.room, selector.role, selector.device_type, selector.requires_capability)
        if value
    ]
    return " · ".join(parts) if parts else "unresolved target"


def rule_to_dict(rule: Rule, *, device_room: str | None = None) -> dict[str, Any]:
    """Render one household rule as a one-line automation row.

    ``device_room`` is the room the device registry resolves the draft's
    ``target_device_id`` to; without it the target falls back to the raw id
    (or the draft's own selector description for selector-based rules). A
    named ``capability`` is authoritative for the action, mirroring how
    ``AuthorityEngine`` classifies a request that names one; otherwise the
    ``ActionKind`` value stands in.
    """

    draft = rule.draft
    source = draft.source_text.strip() or draft.interpretation
    action = draft.capability if draft.capability is not None else draft.action_kind.value
    schedule = draft.schedule_trigger
    return {
        "rule_id": rule.rule_id,
        "summary": _one_line(source),
        "action": action,
        "target": _rule_target(draft, device_room=device_room),
        "target_device_id": draft.target_device_id,
        "capability": draft.capability,
        "parameters": {key: value for key, value in draft.parameters},
        "schedule": (
            {
                "time_of_day": schedule.time_of_day.isoformat(),
                "weekdays": sorted(schedule.weekdays),
            }
            if schedule is not None
            else None
        ),
        "status": rule.status.value,
        "approved_at": rule.approved_at.isoformat() if rule.approved_at is not None else None,
        "approved_by": rule.approved_by,
        "revoked_at": rule.revoked_at.isoformat() if rule.revoked_at is not None else None,
        "revoked_by": rule.revoked_by,
    }


def scheduler_status_to_dict(status: ScheduleStatus) -> dict[str, Any]:
    return {
        "rule_id": status.rule_id,
        "summary": status.summary,
        "enabled": status.enabled,
        "due_now": status.due_now,
        "next_run_at": status.next_run_at,
        "last_fired_at": status.last_fired_at,
        "last_outcome": status.last_outcome,
    }


def engine_to_dict(*, human_override_window: timedelta, minimum_confidence: float) -> dict[str, Any]:
    minutes = human_override_window.total_seconds() / 60
    return {
        "human_override_minutes": int(minutes) if minutes.is_integer() else minutes,
        "minimum_confidence": minimum_confidence,
    }


def provider_to_dict(capabilities: ProviderCapabilities) -> dict[str, Any]:
    return {
        "kind": capabilities.kind,
        "provider_id": capabilities.provider_id,
        "capabilities": sorted(capabilities.capabilities),
    }


def system_to_dict(
    *,
    revision: int,
    event_count: int,
    memory_count: int,
    engine: dict[str, Any],
    providers: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "revision": int(revision),
        "event_count": int(event_count),
        "memory_count": int(memory_count),
        "engine": to_json_value(engine),
        "providers": [to_json_value(provider) for provider in providers],
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
    voice: dict[str, Any] | None = None,
    automations: list[dict[str, Any]] | None = None,
    scheduler: list[dict[str, Any]] | None = None,
    system: dict[str, Any] | None = None,
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
        "automations": [to_json_value(item) for item in (automations or [])],
        "scheduler": [to_json_value(item) for item in (scheduler or [])],
        "status": to_json_value(status),
        "voice": voice if voice is not None else voice_to_dict(state="dormant", mic=False),
        "system": system
        if system is not None
        else system_to_dict(
            revision=0,
            event_count=0,
            memory_count=0,
            engine=engine_to_dict(
                human_override_window=HUMAN_OVERRIDE_WINDOW,
                minimum_confidence=DEFAULT_MINIMUM_CONFIDENCE,
            ),
            providers=[],
        ),
    }


__all__ = [
    "camera_to_dict",
    "context_to_dict",
    "device_to_dict",
    "engine_to_dict",
    "event_to_dict",
    "memory_to_dict",
    "message_to_dict",
    "pending_to_dict",
    "person_to_dict",
    "provider_to_dict",
    "room_to_dict",
    "rule_to_dict",
    "scheduler_status_to_dict",
    "state_to_dict",
    "status_to_dict",
    "system_to_dict",
    "to_json_value",
    "voice_to_dict",
]
