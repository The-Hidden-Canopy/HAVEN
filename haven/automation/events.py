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

**Nothing in this repo emits an `AutomationEvent` yet.** See
`resource_scheduler.py`'s own docstring for exactly what is and is not
wired up this pass -- this module is the shared vocabulary a real emitter
and the scheduler's matcher can agree on, not a running event feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from ..core.time import require_aware_utc


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _params_to_tuple(payload: Mapping[str, Any] | tuple[tuple[str, Any], ...]) -> tuple[tuple[str, Any], ...]:
    if isinstance(payload, Mapping):
        return tuple(payload.items())
    return tuple(payload)


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
    payload: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, name="event_id"))
        object.__setattr__(self, "event_name", _require_text(self.event_name, name="event_name"))
        object.__setattr__(self, "household_id", _require_text(self.household_id, name="household_id"))
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at, name="occurred_at"))
        object.__setattr__(self, "payload", _params_to_tuple(self.payload))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


__all__ = ["AutomationEvent"]
