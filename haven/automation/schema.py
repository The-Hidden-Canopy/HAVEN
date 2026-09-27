"""Domain-independent automation schema (native product-consolidation plan,
P1 "Cross-Domain Automation" §6.2): `Trigger`/`Selector`/`ActionTarget`, the
vocabulary a resource-action scheduler and a unified Automations UI can both
agree on, instead of each domain (home, computer, tasks, comms) inventing
its own recurring-rule shape the way `haven.core.domain.RuleDraft` currently
does for devices alone.

**Contract only, this pass.** Nothing in this repo constructs an
`AutomationSpec` and runs it yet -- that is the separate, larger
"resource-action scheduler entry point" backlog item, which reuses the
existing draft -> edit-while-proposed -> approve -> enable/disable -> revoke
lifecycle (plan §6.1: "reuse it rather than introducing another scheduler")
against `haven.core.domain.Rule`'s own state machine. `RuleDraft` itself is
untouched: rewriting the live, heavily-tested home-automation rule engine to
speak this schema is exactly the kind of large, safety-critical migration
this pass deliberately does not attempt in the same breath as defining the
vocabulary that migration would target.

Each `Trigger`/`Selector`/`ActionTarget` carries a `parameters` bag (a
`tuple[tuple[str, Any], ...]`, the same parameter-bag shape
`RuleDraft.parameters`/`ResourceActionRequest.parameters` already use in
this repo) rather than a rigid union of every kind's own fields -- a time
trigger's "weekdays" and an event trigger's "event_name" genuinely don't
share a shape, and forcing one would recreate the domain coupling this
schema exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..core.consequence import ConsequenceClass


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _params_to_tuple(parameters: Mapping[str, Any] | tuple[tuple[str, Any], ...]) -> tuple[tuple[str, Any], ...]:
    if isinstance(parameters, Mapping):
        return tuple(parameters.items())
    return tuple(parameters)


class TriggerKind(str, Enum):
    """The five trigger families plan §6.2's table names."""

    TIME = "time"
    EVENT = "event"
    EVIDENCE = "evidence"
    DEADLINE = "deadline"
    EXTERNAL_CONDITION = "external_condition"


@dataclass(frozen=True)
class Trigger:
    """What causes the rule to run. Examples per kind (plan §6.2):
    TIME -- weekday/local-time recurrence; EVENT -- new file, new message,
    provider state change, task status change; EVIDENCE -- person present,
    context active, claim confidence threshold; DEADLINE -- task due, reply
    overdue, document renewal; EXTERNAL_CONDITION -- provider-reported
    state, webhook, sync event."""

    kind: TriggerKind
    parameters: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TriggerKind):
            raise ValueError("kind must be a TriggerKind")
        object.__setattr__(self, "parameters", _params_to_tuple(self.parameters))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.parameters)


@dataclass(frozen=True)
class Selector:
    """Narrows *when/what* a trigger applies to -- domain-independent
    condition fields (weekdays, local time, project, room, account, person
    present, evidence-confidence threshold, ...). Distinct from `Trigger`
    the same way a device rule's schedule and its room/person scoping are
    already two separate concerns in `RuleDraft`."""

    parameters: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _params_to_tuple(self.parameters))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.parameters)


@dataclass(frozen=True)
class ActionTarget:
    """What the rule may touch and do. `domain` + `action` name the
    governed operation (e.g. domain="home", action="device.turn_off";
    domain="computer", action="filesystem.move"; domain="tasks",
    action="task.create") -- the same two-part shape `ResourceActionRequest
    .action` and `haven.web.server`'s IPC method names already use, so a
    scheduler built on this schema can dispatch through the *existing*
    governed method for that domain rather than inventing a second
    execution path. `consequence_class` lets the authority layer classify
    by outcome severity rather than by which domain the action came from
    (plan §6.3) -- it is metadata for that decision, not a decision itself;
    this schema grants no authority on its own.
    """

    domain: str
    action: str
    consequence_class: ConsequenceClass
    parameters: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _require_text(self.domain, name="domain"))
        object.__setattr__(self, "action", _require_text(self.action, name="action"))
        if not isinstance(self.consequence_class, ConsequenceClass):
            raise ValueError("consequence_class must be a ConsequenceClass")
        object.__setattr__(self, "parameters", _params_to_tuple(self.parameters))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.parameters)


@dataclass(frozen=True)
class AutomationSpec:
    """One domain-independent automation definition -- trigger + selector +
    action -- shaped to plug into the existing draft/approve/enable/revoke
    lifecycle once a scheduler exists to run it. `source_text` mirrors
    `RuleDraft.source_text`: what the household actually said or chose,
    kept alongside the structured form the same way `RuleDraft` already
    keeps both.
    """

    spec_id: str
    trigger: Trigger
    selector: Selector
    action: ActionTarget
    source_text: str
    created_by: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_id", _require_text(self.spec_id, name="spec_id"))
        if not isinstance(self.trigger, Trigger):
            raise ValueError("trigger must be a Trigger")
        if not isinstance(self.selector, Selector):
            raise ValueError("selector must be a Selector")
        if not isinstance(self.action, ActionTarget):
            raise ValueError("action must be an ActionTarget")
        object.__setattr__(self, "source_text", _require_text(self.source_text, name="source_text"))
        object.__setattr__(self, "created_by", _require_text(self.created_by, name="created_by"))


__all__ = [
    "ActionTarget",
    "AutomationSpec",
    "Selector",
    "Trigger",
    "TriggerKind",
]
