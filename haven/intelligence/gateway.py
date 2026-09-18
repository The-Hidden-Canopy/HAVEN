"""Proposal-only intelligence seam.

Architectural rule: HAVEN is agent-agnostic. An intelligence provider is
selected through the capability registry by kind="intelligence" and the
capabilities a call requires (for example {"interpret"} or
{"chat", "structured_intent"}); HAVEN never asks which vendor or repository
provides it. The scripted fixture registered in `haven.providers.defaults` is
the zero-config default, so HAVEN runs with no intelligence provider
installed.

Every method on this seam is proposal-only. A provider may interpret text,
converse, propose rule drafts, and explain authority decisions, but it never
receives devices, the store, execution adapters, or permissions, and nothing
it returns is executable. Authority and execution stay behind the approval
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from haven.core.domain import ActionKind, AuthorityDecision, Principal, RuleDraft
from haven.core.time import require_aware_utc

from .intents import Intent
from .worldview import WorldView


class UnsupportedIntent(ValueError):
    """Raised when the fixture provider has no declared interpretation."""


@dataclass(frozen=True)
class AgentContext:
    """Bounded, read-only context handed to an intelligence provider.

    Deliberately minimal and serializable: a household, an actor summary, an
    optional room focus, recent conversation lines, and an optional
    `WorldView` -- the bounded, serializable projection of observed world
    state (see `haven.intelligence.worldview`). Providers must never receive
    the store, world internals, or device internals through this object: the
    world view is already-projected evidence with freshness and confidence
    attached, not a window into the snapshot or the device registry.
    """

    household_id: str
    actor_id: str
    actor_role: str
    room_focus: str | None = None
    recent_lines: tuple[str, ...] = ()
    world: WorldView | None = None

    def __post_init__(self) -> None:
        for field_name in ("household_id", "actor_id", "actor_role"):
            if not isinstance(getattr(self, field_name), str) or not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.room_focus is not None and not self.room_focus.strip():
            raise ValueError("room_focus must be a non-empty string when set")
        object.__setattr__(
            self,
            "recent_lines",
            tuple(line for line in self.recent_lines if isinstance(line, str)),
        )
        if self.world is not None and not isinstance(self.world, WorldView):
            raise ValueError("world must be a WorldView or None")

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict; providers receive this across process boundaries."""

        return {
            "household_id": self.household_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "room_focus": self.room_focus,
            "recent_lines": list(self.recent_lines),
            "world": self.world.to_dict() if self.world is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentContext":
        world = data.get("world")
        return cls(
            household_id=data["household_id"],
            actor_id=data["actor_id"],
            actor_role=data["actor_role"],
            room_focus=data.get("room_focus"),
            recent_lines=tuple(data.get("recent_lines", ())),
            world=WorldView.from_dict(world) if world is not None else None,
        )


@dataclass(frozen=True)
class AgentResponse:
    """A conversational reply: text only, nothing executable."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("response text must be a non-empty string")


class IntelligenceProvider(Protocol):
    """Proposal-only intelligence seam.

    HAVEN is agent-agnostic: the provider behind this protocol may be an
    LLM, a deterministic planner, an ensemble, or nothing. Every method
    proposes (a structured draft, a reply, an explanation); none may ever
    reference devices, the store, execution adapters, or permissions, and
    nothing returned here executes. Where a method has no use for a
    parameter yet, the signature stays -- the contract is the point.
    """

    def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft:
        """Return a proposal-only structured interpretation."""

    def chat(self, context: AgentContext, message: str) -> AgentResponse:
        """Return a conversational reply; nothing executable."""

    def propose_rule(self, context: AgentContext, message: str) -> RuleDraft:
        """Return a proposal-only rule draft drawn from a conversation."""

    def explain(self, context: AgentContext, decision: AuthorityDecision) -> AgentResponse:
        """Return a human-facing explanation of an authority decision."""

    def interpret_intent(
        self, text: str, *, context: AgentContext, principal: Principal, now: datetime
    ) -> Intent:
        """Propose exactly ONE intent form for an utterance.

        This is THE agent-agnostic classification seam: an LLM, a planner, a
        deterministic parser, an IDA, or a community provider all propose the
        same `Intent` union, and HAVEN routes it -- a query is answered from
        the world view, an action proposal crosses to authority through the
        direct path, a rule draft enters the propose/approve lifecycle, a
        clarification goes back to the human, and a conversation message gets
        a reply. `interpret`/`propose_rule` remain for structured rule
        drafting; intent classification for arbitrary utterances lives here.
        Nothing returned here executes.
        """


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _require_text(value: str, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


class ScriptedIntelligenceProvider:
    """Deterministic zero-config stand-in for an intelligence provider.

    The example phrase intentionally remains unresolved because "don't blast"
    does not specify a brightness or scene. This makes ambiguity visible to
    the approval boundary instead of turning provider fluency into authority.
    """

    _EXAMPLE = "when i'm working late, don't blast the bedroom lights when i walk in."

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.replace("’", "'").casefold().split())

    def _matches_example(self, text: str) -> bool:
        return self._normalize(text).rstrip(".") == self._EXAMPLE.rstrip(".")

    @staticmethod
    def _draft(*, text: str, household_id: str, actor_id: str) -> RuleDraft:
        return RuleDraft(
            draft_id=_new_id("draft"),
            household_id=household_id,
            proposed_by=actor_id,
            source_text=text,
            interpretation=(
                "When the requester enters the bedroom while the household "
                "context working_late is active, apply a gentler bedroom-light setting."
            ),
            trigger_person_id=actor_id,
            trigger_room_id="bedroom",
            required_context="working_late",
            action_kind=ActionKind.SET_LIGHT_BRIGHTNESS,
            target_device_id="bedroom_lights",
            assumptions=(
                "the speaker is the resident whose presence should trigger the rule",
                "working_late is supplied as an observed household context",
            ),
            unresolved=(
                "the phrase 'don't blast' does not specify a brightness percentage or scene",
            ),
        )

    def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft:
        require_aware_utc(now, name="interpretation time")
        if not self._matches_example(text):
            raise UnsupportedIntent("the fixture provider has no declared interpretation for this text")
        return self._draft(text=text, household_id=principal.household_id, actor_id=principal.actor_id)

    def interpret_intent(
        self, text: str, *, context: AgentContext, principal: Principal, now: datetime
    ) -> Intent:
        require_aware_utc(now, name="interpretation time")
        # Imported here so the interpreter module can import UnsupportedIntent
        # from this module without a circular import.
        from .interpreter import DeterministicIntentInterpreter

        def draft_for(phrase: str, draft_principal: Principal, draft_now: datetime) -> RuleDraft:
            return self.interpret(phrase, principal=draft_principal, now=draft_now)

        interpreter = DeterministicIntentInterpreter(draft_for=draft_for, principal=principal, now=now)
        return interpreter.classify(text, world=context.world, room_focus=context.room_focus)

    def chat(self, context: AgentContext, message: str) -> AgentResponse:
        _require_text(message, name="message")
        if not self._matches_example(message):
            raise UnsupportedIntent("the fixture provider has no canned response for this message")
        return AgentResponse(
            text=(
                "I can turn that into a proposal for a gentler bedroom-light setting "
                "while you're working late, but I won't pick a brightness for you -- "
                "the owner still approves anything I draft."
            )
        )

    def propose_rule(self, context: AgentContext, message: str) -> RuleDraft:
        _require_text(message, name="message")
        if not self._matches_example(message):
            raise UnsupportedIntent("the fixture provider has no declared rule proposal for this text")
        return self._draft(text=message, household_id=context.household_id, actor_id=context.actor_id)

    def explain(self, context: AgentContext, decision: AuthorityDecision) -> AgentResponse:
        return AgentResponse(text=f"{decision.status.value}: {decision.code.value} -- {decision.explanation}")


__all__ = [
    "AgentContext",
    "AgentResponse",
    "IntelligenceProvider",
    "ScriptedIntelligenceProvider",
    "UnsupportedIntent",
]
