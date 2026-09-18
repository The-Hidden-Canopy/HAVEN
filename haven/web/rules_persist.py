"""Rule persistence: the codec and the JSON file store for automations.

The core store is transition-only and in-memory, so automations would live
and die with the process. This module is the seam that lets a real-mode
installation survive a restart: ``rule_to_dict``/``rule_from_dict`` mirror the
frozen domain constructors exactly (mirroring ``haven.devices.manifest``'s
``to_dict``/``from_dict`` idiom -- a payload that cannot rebuild the value is
not persistence), and ``RulesPersistence`` keeps one ``rules.json`` next to
the other setup sidecars.

The file store is deliberately forgiving on read and strict on write: a
missing file means "no automations yet", and one malformed rule row is
skipped rather than allowed to eat the household's other automations. Saving
is atomic (tmp + ``os.replace``), the same idiom as ``_write_json_atomic``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from haven.core.domain import (
    ActionKind,
    DeviceSelector,
    PredictionTrigger,
    RoleTier,
    Rule,
    RuleDraft,
    RuleStatus,
    ScheduleTrigger,
)

_RULES_VERSION = 1
_RULES_FILENAME = "rules.json"


def _require_mapping(payload: object, *, name: str) -> Mapping:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return payload


def _require_keys(payload: Mapping, keys: Iterable[str], *, name: str) -> None:
    for key in keys:
        if key not in payload:
            raise ValueError(f"{name} is missing key: {key!r}")


def _optional_text(payload: Mapping, key: str, *, name: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} {key!r} must be a string or null")
    return value


def _string_list(payload: Mapping, key: str, *, name: str) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} {key!r} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} {key!r} must contain only strings")
    return list(value)


def _datetime_to_str(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_str(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO datetime string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def _schedule_trigger_to_dict(trigger: ScheduleTrigger) -> dict:
    return {
        "time_of_day": trigger.time_of_day.isoformat(),
        "weekdays": sorted(trigger.weekdays),
        "window": trigger.window.total_seconds(),
    }


def _schedule_trigger_from_dict(payload: object) -> ScheduleTrigger:
    payload = _require_mapping(payload, name="schedule trigger payload")
    _require_keys(payload, ("time_of_day", "weekdays", "window"), name="schedule trigger payload")
    raw_time = payload["time_of_day"]
    if not isinstance(raw_time, str):
        raise ValueError("schedule trigger 'time_of_day' must be an HH:MM:SS string")
    try:
        time_of_day = time.fromisoformat(raw_time)
    except ValueError as exc:
        raise ValueError("schedule trigger 'time_of_day' must be an HH:MM:SS string") from exc
    weekdays = payload["weekdays"]
    if not isinstance(weekdays, (list, tuple)):
        raise ValueError("schedule trigger 'weekdays' must be a list")
    window = payload["window"]
    if isinstance(window, bool) or not isinstance(window, (int, float)):
        raise ValueError("schedule trigger 'window' must be a number of seconds")
    return ScheduleTrigger(
        time_of_day=time_of_day,
        weekdays=frozenset(int(day) for day in weekdays),
        window=timedelta(seconds=float(window)),
    )


def _device_selector_to_dict(selector: DeviceSelector) -> dict:
    return {
        "role": selector.role,
        "room": selector.room,
        "device_type": selector.device_type,
        "requires_capability": selector.requires_capability,
    }


def _device_selector_from_dict(payload: object) -> DeviceSelector:
    payload = _require_mapping(payload, name="device selector payload")
    for key in ("role", "room", "device_type", "requires_capability"):
        _optional_text(payload, key, name="device selector")
    return DeviceSelector(
        role=payload.get("role"),
        room=payload.get("room"),
        device_type=payload.get("device_type"),
        requires_capability=payload.get("requires_capability"),
    )


def _prediction_trigger_to_dict(trigger: PredictionTrigger) -> dict:
    return {
        "event": trigger.event,
        "min_confidence": trigger.min_confidence,
        "subject_id": trigger.subject_id,
    }


def _prediction_trigger_from_dict(payload: object) -> PredictionTrigger:
    payload = _require_mapping(payload, name="prediction trigger payload")
    _require_keys(payload, ("event", "min_confidence"), name="prediction trigger payload")
    event = payload["event"]
    if not isinstance(event, str):
        raise ValueError("prediction trigger 'event' must be a string")
    min_confidence = payload["min_confidence"]
    if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)):
        raise ValueError("prediction trigger 'min_confidence' must be a number")
    subject_id = _optional_text(payload, "subject_id", name="prediction trigger")
    return PredictionTrigger(event=event, min_confidence=float(min_confidence), subject_id=subject_id)


def _rule_draft_to_dict(draft: RuleDraft) -> dict:
    return {
        "draft_id": draft.draft_id,
        "household_id": draft.household_id,
        "proposed_by": draft.proposed_by,
        "source_text": draft.source_text,
        "interpretation": draft.interpretation,
        "action_kind": draft.action_kind.value,
        "trigger_person_id": draft.trigger_person_id,
        "trigger_room_id": draft.trigger_room_id,
        "required_context": draft.required_context,
        "prediction_trigger": (
            _prediction_trigger_to_dict(draft.prediction_trigger)
            if draft.prediction_trigger is not None
            else None
        ),
        "schedule_trigger": (
            _schedule_trigger_to_dict(draft.schedule_trigger) if draft.schedule_trigger is not None else None
        ),
        "target_device_id": draft.target_device_id,
        "parameters": [[key, value] for key, value in draft.parameters],
        "assumptions": list(draft.assumptions),
        "unresolved": list(draft.unresolved),
        "capability": draft.capability,
        "target_selector": (
            _device_selector_to_dict(draft.target_selector) if draft.target_selector is not None else None
        ),
        "expires_at": _datetime_to_str(draft.expires_at) if draft.expires_at is not None else None,
    }


def _rule_draft_from_dict(payload: object) -> RuleDraft:
    name = "rule draft payload"
    payload = _require_mapping(payload, name=name)
    _require_keys(
        payload,
        (
            "draft_id",
            "household_id",
            "proposed_by",
            "source_text",
            "interpretation",
            "action_kind",
        ),
        name=name,
    )
    action_kind = payload["action_kind"]
    if not isinstance(action_kind, str) or action_kind not in ActionKind._value2member_map_:
        raise ValueError(f"unknown rule draft action_kind: {action_kind!r}")
    parameters = payload.get("parameters", [])
    if not isinstance(parameters, (list, tuple)):
        raise ValueError("rule draft 'parameters' must be a list")
    pairs: list[tuple[str, Any]] = []
    for item in parameters:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("rule draft 'parameters' must be [key, value] pairs")
        key, value = item
        if not isinstance(key, str):
            raise ValueError("rule draft parameter keys must be strings")
        pairs.append((key, value))
    prediction_payload = payload.get("prediction_trigger")
    schedule_payload = payload.get("schedule_trigger")
    selector_payload = payload.get("target_selector")
    expires_payload = payload.get("expires_at")
    return RuleDraft(
        draft_id=payload["draft_id"],
        household_id=payload["household_id"],
        proposed_by=payload["proposed_by"],
        source_text=payload["source_text"],
        interpretation=payload["interpretation"],
        action_kind=ActionKind(action_kind),
        trigger_person_id=_optional_text(payload, "trigger_person_id", name=name),
        trigger_room_id=_optional_text(payload, "trigger_room_id", name=name),
        required_context=_optional_text(payload, "required_context", name=name),
        prediction_trigger=(
            _prediction_trigger_from_dict(prediction_payload) if prediction_payload is not None else None
        ),
        schedule_trigger=_schedule_trigger_from_dict(schedule_payload) if schedule_payload is not None else None,
        target_device_id=_optional_text(payload, "target_device_id", name=name),
        parameters=tuple(pairs),
        assumptions=tuple(_string_list(payload, "assumptions", name=name)),
        unresolved=tuple(_string_list(payload, "unresolved", name=name)),
        capability=_optional_text(payload, "capability", name=name),
        target_selector=_device_selector_from_dict(selector_payload) if selector_payload is not None else None,
        expires_at=_datetime_from_str(expires_payload, name="rule draft 'expires_at'") if expires_payload is not None else None,
    )


def rule_to_dict(rule: Rule) -> dict:
    return {
        "rule_id": rule.rule_id,
        "draft": _rule_draft_to_dict(rule.draft),
        "status": rule.status.value,
        "approved_by": rule.approved_by,
        "approved_by_role": rule.approved_by_role.value if rule.approved_by_role is not None else None,
        "approved_at": _datetime_to_str(rule.approved_at) if rule.approved_at is not None else None,
    }


def rule_from_dict(payload: Mapping) -> Rule:
    """Rebuild one rule through the frozen constructors; bad shape -> ValueError."""

    if not isinstance(payload, Mapping):
        raise ValueError("rule payload must be a mapping")
    _require_keys(payload, ("rule_id", "draft", "status"), name="rule payload")
    status = payload["status"]
    if not isinstance(status, str) or status not in RuleStatus._value2member_map_:
        raise ValueError(f"unknown rule status: {status!r}")
    approved_by_role = payload.get("approved_by_role")
    if approved_by_role is not None:
        try:
            approved_by_role = RoleTier(approved_by_role)
        except ValueError as exc:
            raise ValueError(f"unknown rule approved_by_role: {approved_by_role!r}") from exc
    approved_by = payload.get("approved_by")
    if approved_by is not None and not isinstance(approved_by, str):
        raise ValueError("rule 'approved_by' must be a string or null")
    approved_at = payload.get("approved_at")
    try:
        return Rule(
            rule_id=payload["rule_id"],
            draft=_rule_draft_from_dict(payload["draft"]),
            status=RuleStatus(status),
            approved_by=approved_by,
            approved_by_role=approved_by_role,
            approved_at=_datetime_from_str(approved_at, name="rule 'approved_at'") if approved_at is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid rule payload: {exc}") from exc


@dataclass(frozen=True)
class RulesSnapshot:
    """One loaded ``rules.json``: the automations and the scheduler enabled map."""

    rules: tuple[Rule, ...] = ()
    scheduler_enabled: dict[str, bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "scheduler_enabled", dict(self.scheduler_enabled or {}))


class RulesPersistence:
    """The ``rules.json`` sidecar: forgiving on read, atomic on write.

    Reading NEVER raises: a missing file means "no automations yet", a
    malformed document degrades to nothing, and one bad rule row is skipped
    while the good rows still load -- a single corrupted automation must not
    eat the household's other automations.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> RulesSnapshot:
        try:
            raw = self._path.read_bytes()
        except OSError:
            return RulesSnapshot()
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return RulesSnapshot()
        if not isinstance(data, Mapping):
            return RulesSnapshot()
        rules: list[Rule] = []
        rows = data.get("rules", [])
        if isinstance(rows, (list, tuple)):
            for row in rows:
                try:
                    rules.append(rule_from_dict(row))
                except ValueError:
                    continue  # one bad row must not eat the good ones
        enabled: dict[str, bool] = {}
        raw_enabled = data.get("scheduler_enabled", {})
        if isinstance(raw_enabled, Mapping):
            for rule_id, value in raw_enabled.items():
                if isinstance(rule_id, str) and isinstance(value, bool):
                    enabled[rule_id] = value
        return RulesSnapshot(rules=tuple(rules), scheduler_enabled=enabled)

    def save(self, rules: Iterable[Rule], scheduler_enabled: Mapping[str, bool] | None = None) -> None:
        payload = {
            "version": _RULES_VERSION,
            "rules": [rule_to_dict(rule) for rule in rules],
            "scheduler_enabled": {
                str(rule_id): bool(enabled) for rule_id, enabled in (scheduler_enabled or {}).items()
            },
        }
        tmp = self._path.with_name(self._path.name + ".tmp")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = [
    "RulesPersistence",
    "RulesSnapshot",
    "rule_from_dict",
    "rule_to_dict",
]
