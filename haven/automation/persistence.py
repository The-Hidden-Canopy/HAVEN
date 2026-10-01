"""Durable state for resource-action automations.

This sidecar is intentionally separate from ``rules.json``: that file stores
the device vertical's ``Rule`` values, while this store persists the
domain-independent ``AutomationRule`` values and their event-driven runtime
state. Reads are forgiving (one malformed row is skipped); writes are strict
and atomic. Secrets are not accepted by the codec.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.domain import DecisionStatus, RoleTier, RuleStatus, EvidenceStatus
from ..core.consequence import ConsequenceClass
from .events import AutomationEvent
from .lifecycle import AutomationLifecycleEvent, AutomationRule, rehydrate
from .schema import ActionTarget, AutomationSpec, Selector, Trigger, TriggerKind

_VERSION = 1


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__type__": "time", "value": value.isoformat()}
    if isinstance(value, Enum):
        return {"__type__": "enum", "value": value.value}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("automation parameter mappings must use string keys")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError(f"unsupported automation parameter value: {type(value).__name__}")


def _from_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_from_json_value(item) for item in value]
    if isinstance(value, dict):
        marker = value.get("__type__")
        if marker == "datetime":
            return _datetime_from_str(value.get("value"), name="automation datetime")
        if marker == "time":
            raw = value.get("value")
            if not isinstance(raw, str):
                raise ValueError("automation time must be an ISO string")
            return time.fromisoformat(raw)
        if marker == "enum":
            # Generic parameter enums are persisted as their value. Typed
            # fields below validate against their own enum explicitly.
            return value.get("value")
        if marker is not None:
            raise ValueError(f"unknown automation value marker: {marker!r}")
        return {key: _from_json_value(item) for key, item in value.items()}
    return value


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


def _parameter_pairs_to_dict(parameters: tuple[tuple[str, Any], ...]) -> list[list[Any]]:
    return [[key, _json_value(value)] for key, value in parameters]


def _parameter_pairs_from_payload(payload: object, *, name: str) -> tuple[tuple[str, Any], ...]:
    if not isinstance(payload, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    result: list[tuple[str, Any]] = []
    for item in payload:
        if not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[0], str):
            raise ValueError(f"{name} must contain [string, value] pairs")
        result.append((item[0], _from_json_value(item[1])))
    return tuple(result)


def spec_to_dict(spec: AutomationSpec) -> dict[str, Any]:
    return {
        "spec_id": spec.spec_id,
        "household_id": spec.household_id,
        "trigger": {
            "kind": spec.trigger.kind.value,
            "parameters": _parameter_pairs_to_dict(spec.trigger.parameters),
        },
        "selector": {"parameters": _parameter_pairs_to_dict(spec.selector.parameters)},
        "action": {
            "domain": spec.action.domain,
            "action": spec.action.action,
            "consequence_class": spec.action.consequence_class.value,
            "parameters": _parameter_pairs_to_dict(spec.action.parameters),
        },
        "source_text": spec.source_text,
        "created_by": spec.created_by,
    }


def spec_from_dict(payload: object) -> AutomationSpec:
    if not isinstance(payload, Mapping):
        raise ValueError("automation spec must be an object")
    trigger = payload.get("trigger")
    selector = payload.get("selector")
    action = payload.get("action")
    if not isinstance(trigger, Mapping) or not isinstance(selector, Mapping) or not isinstance(action, Mapping):
        raise ValueError("automation spec components must be objects")
    kind = trigger.get("kind")
    consequence = action.get("consequence_class")
    if not isinstance(kind, str) or kind not in TriggerKind._value2member_map_:
        raise ValueError("unknown automation trigger kind")
    if not isinstance(consequence, str) or consequence not in ConsequenceClass._value2member_map_:
        raise ValueError("unknown automation consequence class")
    return AutomationSpec(
        spec_id=payload["spec_id"],
        household_id=payload["household_id"],
        trigger=Trigger(
            kind=TriggerKind(kind),
            parameters=_parameter_pairs_from_payload(trigger.get("parameters", []), name="trigger parameters"),
        ),
        selector=Selector(
            parameters=_parameter_pairs_from_payload(selector.get("parameters", []), name="selector parameters")
        ),
        action=ActionTarget(
            domain=action["domain"],
            action=action["action"],
            consequence_class=ConsequenceClass(consequence),
            parameters=_parameter_pairs_from_payload(action.get("parameters", []), name="action parameters"),
        ),
        source_text=payload["source_text"],
        created_by=payload["created_by"],
    )


def rule_to_dict(rule: AutomationRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "spec": spec_to_dict(rule.spec),
        "status": rule.status.value,
        "enabled": rule.enabled,
        "approved_by": rule.approved_by,
        "approved_by_role": rule.approved_by_role.value if rule.approved_by_role is not None else None,
        "approved_at": rule.approved_at.isoformat() if rule.approved_at is not None else None,
        "revoked_by": rule.revoked_by,
        "revoked_at": rule.revoked_at.isoformat() if rule.revoked_at is not None else None,
    }


def rule_from_dict(payload: object) -> AutomationRule:
    if not isinstance(payload, Mapping):
        raise ValueError("automation rule must be an object")
    status = payload.get("status")
    role = payload.get("approved_by_role")
    if not isinstance(status, str) or status not in RuleStatus._value2member_map_:
        raise ValueError("unknown automation rule status")
    if role is not None and (isinstance(role, bool) or not isinstance(role, int) or role not in RoleTier._value2member_map_):
        raise ValueError("unknown automation approved role")
    return rehydrate(
        rule_id=payload["rule_id"],
        spec=spec_from_dict(payload["spec"]),
        status=RuleStatus(status),
        enabled=payload.get("enabled", True),
        approved_by=payload.get("approved_by"),
        approved_by_role=RoleTier(role) if role is not None else None,
        approved_at=_datetime_from_str(payload["approved_at"], name="approved_at")
        if payload.get("approved_at") is not None
        else None,
        revoked_by=payload.get("revoked_by"),
        revoked_at=_datetime_from_str(payload["revoked_at"], name="revoked_at")
        if payload.get("revoked_at") is not None
        else None,
    )


def lifecycle_event_to_dict(event: AutomationLifecycleEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "transition": event.transition,
        "household_id": event.household_id,
        "rule_id": event.rule_id,
        "actor_id": event.actor_id,
        "status": event.status.value,
        "enabled": event.enabled,
        "justification": event.justification,
        "occurred_at": event.occurred_at.isoformat(),
        "correlation_id": event.correlation_id,
    }


def lifecycle_event_from_dict(payload: object) -> AutomationLifecycleEvent:
    if not isinstance(payload, Mapping):
        raise ValueError("automation lifecycle event must be an object")
    status = payload.get("status")
    if not isinstance(status, str) or status not in DecisionStatus._value2member_map_:
        raise ValueError("unknown automation lifecycle decision status")
    return AutomationLifecycleEvent(
        event_id=payload["event_id"],
        transition=payload["transition"],
        household_id=payload["household_id"],
        rule_id=payload["rule_id"],
        actor_id=payload["actor_id"],
        status=DecisionStatus(status),
        enabled=payload["enabled"],
        justification=payload["justification"],
        occurred_at=_datetime_from_str(payload["occurred_at"], name="lifecycle event time"),
        correlation_id=payload["correlation_id"],
    )


def event_to_dict(event: AutomationEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_name": event.event_name,
        "household_id": event.household_id,
        "occurred_at": event.occurred_at.isoformat(),
        "source": event.source,
        "payload": _parameter_pairs_to_dict(event.payload),
        "evidence_status": event.evidence_status.value,
    }


def event_from_dict(payload: object) -> AutomationEvent:
    if not isinstance(payload, Mapping):
        raise ValueError("automation event must be an object")
    status = payload.get("evidence_status", EvidenceStatus.OBSERVED.value)
    if not isinstance(status, str) or status not in EvidenceStatus._value2member_map_:
        raise ValueError("unknown automation evidence status")
    return AutomationEvent.restore_published(
        event_id=payload["event_id"],
        event_name=payload["event_name"],
        household_id=payload["household_id"],
        occurred_at=_datetime_from_str(payload["occurred_at"], name="automation event time"),
        source=payload["source"],
        payload=_parameter_pairs_from_payload(payload.get("payload", []), name="event payload"),
        evidence_status=EvidenceStatus(status),
    )


@dataclass(frozen=True)
class ResourceAutomationSnapshot:
    rules: tuple[AutomationRule, ...] = ()
    lifecycle_events: tuple[AutomationLifecycleEvent, ...] = ()
    pending_events: tuple[AutomationEvent, ...] = ()
    scheduler_state: dict[str, Any] | None = None


class ResourceAutomationStore:
    """Atomic JSON persistence for resource automations and runtime state."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ResourceAutomationSnapshot:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ResourceAutomationSnapshot()
        if not isinstance(data, Mapping):
            return ResourceAutomationSnapshot()
        rules: list[AutomationRule] = []
        for row in data.get("rules", ()) if isinstance(data.get("rules", ()), (list, tuple)) else ():
            try:
                rules.append(rule_from_dict(row))
            except (TypeError, ValueError, KeyError):
                continue
        events: list[AutomationLifecycleEvent] = []
        for row in data.get("lifecycle_events", ()) if isinstance(data.get("lifecycle_events", ()), (list, tuple)) else ():
            try:
                events.append(lifecycle_event_from_dict(row))
            except (TypeError, ValueError, KeyError):
                continue
        pending: list[AutomationEvent] = []
        for row in data.get("pending_events", ()) if isinstance(data.get("pending_events", ()), (list, tuple)) else ():
            try:
                pending.append(event_from_dict(row))
            except (TypeError, ValueError, KeyError):
                continue
        state = data.get("scheduler_state", {})
        if isinstance(state, Mapping):
            try:
                state = _from_json_value(state)
            except ValueError:
                state = {}
        return ResourceAutomationSnapshot(
            rules=tuple(rules), lifecycle_events=tuple(events), pending_events=tuple(pending),
            scheduler_state=dict(state) if isinstance(state, Mapping) else {},
        )

    def save(self, snapshot: ResourceAutomationSnapshot) -> None:
        payload = {
            "version": _VERSION,
            "rules": [rule_to_dict(rule) for rule in snapshot.rules],
            "lifecycle_events": [lifecycle_event_to_dict(event) for event in snapshot.lifecycle_events],
            "pending_events": [event_to_dict(event) for event in snapshot.pending_events],
            "scheduler_state": _json_value(snapshot.scheduler_state or {}),
        }
        raw = json.dumps(payload, indent=2) + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(raw, encoding="utf-8")
            os.replace(tmp, self._path)


__all__ = [
    "ResourceAutomationSnapshot",
    "ResourceAutomationStore",
    "event_from_dict",
    "event_to_dict",
    "lifecycle_event_from_dict",
    "lifecycle_event_to_dict",
    "rule_from_dict",
    "rule_to_dict",
    "spec_from_dict",
    "spec_to_dict",
]
