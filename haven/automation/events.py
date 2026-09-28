"""`AutomationEvent`: one concrete occurrence a `TriggerKind.EVENT`
automation can react to -- a new file, a new message, a provider state
change, a task status change (plan §6.2's own examples for this trigger
kind, and the exact four named in the "task/file/comms event triggers"
backlog item).

Deliberately one generic shape rather than four domain-specific event
classes: `event_name` plus a `payload` parameter bag (the same
`tuple[tuple[str, Any], ...]` shape `Trigger`/`Selector`/`ActionTarget`
already use) lets `ResourceActionScheduler.handle_events` match against it
generically, and lets a future emitter (a computer scan noticing a new
resource, an email poll noticing a new message, a provider health check
noticing a state change, a task update crossing a status boundary) produce
one without this module needing to know anything about where it came from --
the same open-vocabulary discipline `ResourceActionRequest.action` already
uses for provider actions.

No production domain adapter emits an `AutomationEvent` yet. The
`AutomationEventPublisher` added here is the trusted, household-scoped
construction boundary that a future adapter must receive; a raw event value
is intentionally ineligible for scheduler execution.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from ..core.domain import EvidenceStatus
from ..core.time import require_aware_utc


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _params_to_tuple(payload: Mapping[str, Any] | tuple[tuple[str, Any], ...]) -> tuple[tuple[str, Any], ...]:
    items = payload.items() if isinstance(payload, Mapping) else payload
    # A frozen dataclass only freezes the outer tuple. Snapshot nested values
    # too, otherwise a provider can mutate the mapping it passed after the
    # event was published and silently rewrite the evidence seen by a later
    # matcher or audit reader.
    return tuple((key, deepcopy(value)) for key, value in items)


_PUBLISHER_TOKEN = object()


@dataclass(frozen=True)
class AutomationEvent:
    """One occurrence, e.g. `event_name="file.created"` with a payload
    naming the resource. `event_id` is this occurrence's own identity, used
    for delivery dedup by `ResourceActionScheduler.handle_events` -- an
    at-least-once emitter redelivering the same event (e.g. after a
    restart) must not refire an automation a second time."""

    event_id: str
    event_name: str
    household_id: str
    occurred_at: datetime
    source: str = "untrusted"
    payload: tuple[tuple[str, Any], ...] = ()
    evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED
    _publisher_token: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, name="event_id"))
        object.__setattr__(self, "event_name", _require_text(self.event_name, name="event_name"))
        object.__setattr__(self, "household_id", _require_text(self.household_id, name="household_id"))
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at, name="occurred_at"))
        object.__setattr__(self, "source", _require_text(self.source, name="source"))
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ValueError("evidence_status must be an EvidenceStatus")
        object.__setattr__(self, "payload", _params_to_tuple(self.payload))

    @property
    def is_trusted(self) -> bool:
        """Whether this occurrence came through an explicit publisher boundary."""

        return self._publisher_token is _PUBLISHER_TOKEN

    @property
    def is_eligible_for_automation(self) -> bool:
        """Whether this event carries usable, non-degraded evidence."""

        return self.is_trusted and self.evidence_status not in {
            EvidenceStatus.STALE,
            EvidenceStatus.FALLBACK,
            EvidenceStatus.UNAVAILABLE,
        }

    def as_dict(self) -> dict[str, Any]:
        # Do not hand callers aliases into the event's retained evidence.
        return deepcopy(dict(self.payload))


@dataclass(frozen=True)
class AutomationEventPublisher:
    """Scoped source boundary for creating scheduler-eligible events.

    A raw ``AutomationEvent`` is useful as a transport/value object but is
    deliberately not trusted by ``ResourceActionScheduler``. Domain adapters
    receive a publisher bound to one household and source, then publish only
    events they actually observed.
    """

    source: str
    household_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _require_text(self.source, name="source"))
        object.__setattr__(self, "household_id", _require_text(self.household_id, name="household_id"))

    def publish(
        self,
        *,
        event_id: str,
        event_name: str,
        occurred_at: datetime,
        payload: Mapping[str, Any] | tuple[tuple[str, Any], ...] = (),
        evidence_status: EvidenceStatus = EvidenceStatus.OBSERVED,
    ) -> AutomationEvent:
        return AutomationEvent(
            event_id=event_id,
            event_name=event_name,
            household_id=self.household_id,
            occurred_at=occurred_at,
            source=self.source,
            payload=payload,
            evidence_status=evidence_status,
            _publisher_token=_PUBLISHER_TOKEN,
        )


__all__ = ["AutomationEvent", "AutomationEventPublisher"]
