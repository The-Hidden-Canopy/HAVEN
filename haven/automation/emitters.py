"""Production observation adapters for the domain-independent event feed.

Each adapter translates one existing HAVEN observation/mutation shape into the
trusted ``AutomationEventFeed`` vocabulary. Adapters carry no authority and
execute no action; ``ResourceActionScheduler`` remains the sole consumer that
can ask a domain's governed action service to run a matching rule.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
from typing import Any

from ..core.domain import ContextState, DeviceState, EvidenceStatus, PresenceState
from ..domains.tasks.models import TaskRecord
from ..integrations.comms.email import EmailMessage
from ..resources.models import ResourceRecord
from .events import AutomationEvent, AutomationEventFeed


def _task_payload(before: TaskRecord | None, after: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": after.task_id,
        "scope_id": after.scope_id,
        "from_state": before.state if before is not None else None,
        "to_state": after.state,
        "project_id": after.project_id,
        "title": after.title,
        "revision": after.revision,
    }


class TaskAutomationEmitter:
    """Publishes task creation and status-boundary events."""

    def __init__(self, feed: AutomationEventFeed) -> None:
        self._feed = feed

    def changed(self, before: TaskRecord | None, after: TaskRecord) -> AutomationEvent | None:
        if not isinstance(after, TaskRecord):
            raise TypeError("after must be a TaskRecord")
        if before is not None and not isinstance(before, TaskRecord):
            raise TypeError("before must be a TaskRecord or None")
        if before is not None and before.state == after.state:
            return None
        created = before is None
        return self._feed.publish(
            event_id=f"task:{after.task_id}:revision:{after.revision}",
            event_name="task.created" if created else "task.status_changed",
            occurred_at=after.updated_at,
            payload=_task_payload(before, after),
            evidence_status=EvidenceStatus.OBSERVED,
        )


class ComputerResourceAutomationEmitter:
    """Publishes first-seen filesystem/resource observations."""

    def __init__(self, feed: AutomationEventFeed) -> None:
        self._feed = feed

    def new_resources(
        self,
        records: Iterable[ResourceRecord],
        *,
        is_new: Callable[[str], bool],
    ) -> tuple[AutomationEvent, ...]:
        events: list[AutomationEvent] = []
        for record in records:
            if not isinstance(record, ResourceRecord) or not is_new(record.resource_id):
                continue
            events.append(
                self._feed.publish(
                    event_id=f"resource:{record.resource_id}:observed:{record.observed_at.isoformat()}",
                    event_name="file.created" if record.resource_type in {"file", "document"} else "resource.created",
                    occurred_at=record.observed_at,
                    payload={
                        "resource_id": record.resource_id,
                        "resource_type": record.resource_type,
                        "scope_id": record.scope_id,
                        "provider_id": record.provider_id,
                        "title": record.title,
                        "locator": record.locator,
                    },
                    evidence_status=EvidenceStatus.OBSERVED,
                )
            )
        return tuple(events)


class EmailAutomationEmitter:
    """Publishes newly observed mailbox messages without indexing full bodies."""

    def __init__(self, feed: AutomationEventFeed) -> None:
        self._feed = feed

    def new_messages(
        self,
        messages: Iterable[EmailMessage],
        *,
        is_new: Callable[[str], bool],
        occurred_at=None,
    ) -> tuple[AutomationEvent, ...]:
        events: list[AutomationEvent] = []
        for message in messages:
            if not isinstance(message, EmailMessage):
                continue
            message_id = message.message_id.strip("<>")
            if not message_id or not is_new(message_id):
                continue
            events.append(
                self._feed.publish(
                    event_id=f"email:{message_id}",
                    event_name="message.received",
                    occurred_at=occurred_at,
                    payload={
                        "message_id": message_id,
                        "sender": message.sender,
                        "subject": message.subject,
                        "thread_id": message.thread_id.strip("<>"),
                        "recipients": list(message.recipients),
                    },
                    evidence_status=EvidenceStatus.OBSERVED,
                )
            )
        return tuple(events)


class ProviderHealthAutomationEmitter:
    """Publishes only provider reachability transitions."""

    def __init__(self, feed: AutomationEventFeed) -> None:
        self._feed = feed
        self._last: dict[str, bool] = {}

    def changed(
        self,
        *,
        provider_id: str,
        reachable: bool,
        detail: str,
        occurred_at=None,
    ) -> AutomationEvent | None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(reachable, bool):
            raise ValueError("reachable must be a bool")
        previous = self._last.get(provider_id)
        self._last[provider_id] = reachable
        if previous is not None and previous == reachable:
            return None
        from_state = "reachable" if previous is True else "unavailable" if previous is False else None
        to_state = "reachable" if reachable else "unavailable"
        return self._feed.publish(
            event_id=f"provider:{provider_id}:health:{to_state}",
            event_name="provider.health_changed",
            occurred_at=occurred_at,
            payload={
                "provider_id": provider_id,
                "from_state": from_state,
                "to_state": to_state,
                "reachable": reachable,
                "detail": str(detail),
            },
            evidence_status=EvidenceStatus.OBSERVED,
        )


class EvidenceAutomationEmitter:
    """Publish fused presence/context/device evidence for automation.

    The emitter accepts only HAVEN's typed evidence values, keeps the
    observation payload bounded, and leaves freshness/confidence decisions to
    the scheduler's trigger matcher. It does not read a store or execute an
    action; a real observation provider can call it after its normal fusion
    and evidence validation step.
    """

    def __init__(self, feed: AutomationEventFeed) -> None:
        self._feed = feed

    @staticmethod
    def _payload(state: PresenceState | ContextState | DeviceState) -> tuple[str, str, dict[str, Any]]:
        if isinstance(state, PresenceState):
            return (
                "presence",
                state.person_id,
                {
                    "person_id": state.person_id,
                    "room_id": state.room_id,
                    "present": state.present,
                    "value": state.present,
                    "confidence": state.confidence,
                    "source": state.source,
                },
            )
        if isinstance(state, ContextState):
            return (
                "context",
                state.context_id,
                {
                    "context_id": state.context_id,
                    "active": state.active,
                    "value": state.active,
                    "confidence": state.confidence,
                    "source": state.source,
                },
            )
        if isinstance(state, DeviceState):
            return (
                "device",
                state.device_id,
                {
                    "device_id": state.device_id,
                    "kind": state.kind,
                    "room_id": state.room_id,
                    "is_on": state.is_on,
                    "brightness_pct": state.brightness_pct,
                    "cover_state": state.cover_state.value if state.cover_state is not None else None,
                    "lock_state": state.lock_state.value if state.lock_state is not None else None,
                    "climate_mode": state.climate_mode,
                    "current_temperature": state.current_temperature,
                    "target_temperature": state.target_temperature,
                    "camera_available": state.camera_available,
                    "motion_detected": state.motion_detected,
                    "value": state.raw_state if state.raw_state is not None else state.is_on,
                    "confidence": state.confidence,
                    "source": state.source,
                },
            )
        raise TypeError("state must be PresenceState, ContextState, or DeviceState")

    def changed(
        self,
        state: PresenceState | ContextState | DeviceState,
        *,
        occurred_at=None,
    ) -> AutomationEvent:
        kind, subject_id, payload = self._payload(state)
        payload.update({"evidence_kind": kind, "subject_id": subject_id})
        return self._feed.publish(
            event_id=f"evidence:{kind}:{subject_id}:{state.observed_at.isoformat()}:{state.source}",
            event_name="evidence.changed",
            occurred_at=occurred_at if occurred_at is not None else state.observed_at,
            payload=payload,
            evidence_status=state.status,
        )


_MISSING = object()


class ExternalConditionAutomationEmitter:
    """Publish provider-reported condition transitions.

    Repeated observations with the same provider, condition, and value are
    coalesced. The first observation is still published so a newly-started
    process can establish the current condition; degraded evidence is carried
    through and rejected by the scheduler rather than treated as a false
    condition.
    """

    def __init__(self, feed: AutomationEventFeed) -> None:
        self._feed = feed
        self._last: dict[tuple[str, str], tuple[Any, EvidenceStatus]] = {}
        self._sequence = 0

    def changed(
        self,
        *,
        provider_id: str,
        condition: str,
        value: Any,
        evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED,
        occurred_at=None,
    ) -> AutomationEvent | None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError("condition must be a non-empty string")
        if not isinstance(evidence_status, EvidenceStatus):
            raise ValueError("evidence_status must be an EvidenceStatus")
        key = (provider_id.strip(), condition.strip())
        previous = self._last.get(key, _MISSING)
        if previous is not _MISSING and previous[0] == value and previous[1] is evidence_status:
            return None
        previous_value = previous[0] if previous is not _MISSING else _MISSING
        self._last[key] = (deepcopy(value), evidence_status)
        self._sequence += 1
        payload: dict[str, Any] = {"provider_id": key[0], "condition": key[1], "value": deepcopy(value)}
        if previous_value is not _MISSING:
            payload["previous_value"] = deepcopy(previous_value)
        return self._feed.publish(
            event_id=f"external:{key[0]}:{key[1]}:{self._sequence}",
            event_name="external.condition.changed",
            occurred_at=occurred_at,
            payload=payload,
            evidence_status=evidence_status,
        )


__all__ = [
    "ComputerResourceAutomationEmitter",
    "EvidenceAutomationEmitter",
    "EmailAutomationEmitter",
    "ExternalConditionAutomationEmitter",
    "ProviderHealthAutomationEmitter",
    "TaskAutomationEmitter",
]
