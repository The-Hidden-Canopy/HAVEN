"""Production observation adapters for the domain-independent event feed.

Each adapter translates one existing HAVEN observation/mutation shape into the
trusted ``AutomationEventFeed`` vocabulary. Adapters carry no authority and
execute no action; ``ResourceActionScheduler`` remains the sole consumer that
can ask a domain's governed action service to run a matching rule.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..core.domain import EvidenceStatus
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


__all__ = [
    "ComputerResourceAutomationEmitter",
    "EmailAutomationEmitter",
    "ProviderHealthAutomationEmitter",
    "TaskAutomationEmitter",
]
