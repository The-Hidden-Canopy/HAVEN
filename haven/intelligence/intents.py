"""The natural-language intent union.

An utterance is not always an automation rule. The interpreter behind
`haven.intelligence.gateway.IntelligenceProvider` -- an agent or a
deterministic parser -- proposes exactly ONE of these forms, and routing
differs per form: a query is answered from the world view, a direct action
goes straight to authority (`HavenRuntime.run_action`), a rule draft enters
the proposal-and-approval lifecycle, a clarification request goes back to
the human, and a plain conversation message gets a reply. Nothing here
executes: every executable form still crosses `AuthorityEngine` before any
device command is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from haven.core.domain import ActionKind, DeviceSelector, IntentForm, RuleDraft


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _normalize_parameters(
    parameters: Mapping[str, Any] | Iterable[tuple[str, Any]],
) -> tuple[tuple[str, Any], ...]:
    items = tuple(parameters.items()) if isinstance(parameters, Mapping) else tuple(parameters)
    normalized = []
    seen: set[str] = set()
    for key, value in items:
        key = _require_text(key, name="parameter name")
        if key in seen:
            raise ValueError(f"duplicate parameter: {key}")
        seen.add(key)
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda item: item[0]))


@dataclass(frozen=True)
class QueryRequest(IntentForm):
    """A question about the world ("is the garage still open?").

    Carries only the raw text: which answerer handles it (an agent reading
    a `WorldView`, or a deterministic lookup fallback) is chosen downstream.
    """

    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_text(self.text, name="query text"))


@dataclass(frozen=True)
class ActionProposal(IntentForm):
    """A one-shot command, proposed by an interpreter from source text.

    Exactly one of `target_device_id` / `target_selector` must be set,
    mirroring `RuleDraft`'s invariant: the proposal addresses one device
    either by name or by a selector the runtime resolves at execution time.
    Unlike a rule draft this carries no trigger -- a direct action is
    executed once, now, by the member who asked.
    """

    action_kind: ActionKind
    target_device_id: str | None
    target_selector: DeviceSelector | None
    parameters: tuple[tuple[str, Any], ...]
    justification: str
    source_text: str
    capability: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_kind, ActionKind):
            raise ValueError("action_kind must be an ActionKind")
        if (self.target_device_id is None) == (self.target_selector is None):
            raise ValueError("an action proposal must set exactly one of target_device_id or target_selector")
        if self.target_device_id is not None:
            object.__setattr__(
                self, "target_device_id", _require_text(self.target_device_id, name="target_device_id")
            )
        if self.target_selector is not None and not isinstance(self.target_selector, DeviceSelector):
            raise ValueError("target_selector must be a DeviceSelector")
        if self.capability is not None:
            object.__setattr__(self, "capability", _require_text(self.capability, name="capability"))
        object.__setattr__(self, "parameters", _normalize_parameters(self.parameters))
        object.__setattr__(self, "justification", _require_text(self.justification, name="justification"))
        object.__setattr__(self, "source_text", _require_text(self.source_text, name="source_text"))


@dataclass(frozen=True)
class MutationProposal(IntentForm):
    """A proposed change to HAVEN's declared context.

    This is deliberately a proposal, not a command.  Parsing language into
    one of these objects has no side effects; a composition/API layer must
    validate the target and apply it through the appropriate authoring
    service.  Keeping the boundary explicit prevents a model or parser from
    writing household configuration directly.

    The first public mutation surface covers declarations that are already
    owned by ``SetupService``: rooms, people, and contexts.  It also carries
    explicitly parsed automation authoring attributes; the application layer
    resolves their device target and creates a proposed ``RuleDraft``. The
    open attribute tuple leaves room for richer entity-specific fields
    without turning this union into a second persistence schema.
    """

    entity_kind: str
    operation: str
    attributes: tuple[tuple[str, Any], ...]
    source_text: str
    target_id: str | None = None

    _ENTITY_KINDS = frozenset({"room", "person", "context", "automation"})
    _OPERATIONS = frozenset({"create", "rename", "update", "delete"})

    def __post_init__(self) -> None:
        entity_kind = _require_text(self.entity_kind, name="entity_kind").casefold()
        operation = _require_text(self.operation, name="operation").casefold()
        if entity_kind not in self._ENTITY_KINDS:
            raise ValueError(f"unsupported mutation entity kind: {entity_kind}")
        if operation not in self._OPERATIONS:
            raise ValueError(f"unsupported mutation operation: {operation}")
        if operation == "create" and self.target_id is not None:
            raise ValueError("a create mutation cannot have a target_id")
        if operation != "create" and self.target_id is not None:
            object.__setattr__(self, "target_id", _require_text(self.target_id, name="target_id"))
        object.__setattr__(self, "entity_kind", entity_kind)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "attributes", _normalize_parameters(self.attributes))
        object.__setattr__(self, "source_text", _require_text(self.source_text, name="source_text"))


# A rule proposal reuses the existing draft directly -- no wrapper.
RuleProposal = RuleDraft


@dataclass(frozen=True)
class ClarificationRequest(IntentForm):
    """The interpreter cannot resolve the request and must ask the human.

    `options` may offer candidate resolutions; an empty tuple means the
    question is open-ended.
    """

    question: str
    source_text: str
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _require_text(self.question, name="clarification question"))
        object.__setattr__(self, "source_text", _require_text(self.source_text, name="source_text"))
        object.__setattr__(
            self, "options", tuple(_require_text(option, name="clarification option") for option in self.options)
        )


@dataclass(frozen=True)
class ConversationMessage(IntentForm):
    """Neither a question about the world nor a command: greetings, small
    talk, anything the interpreter answers conversationally."""

    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_text(self.text, name="message text"))


Intent = QueryRequest | ActionProposal | MutationProposal | RuleProposal | ClarificationRequest | ConversationMessage


__all__ = [
    "ActionProposal",
    "ClarificationRequest",
    "ConversationMessage",
    "Intent",
    "IntentForm",
    "MutationProposal",
    "QueryRequest",
    "RuleProposal",
]
