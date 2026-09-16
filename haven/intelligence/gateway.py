"""A model gateway that can propose structure but cannot execute actions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import uuid4

from haven.core.domain import ActionKind, Principal, RuleDraft
from haven.core.time import require_aware_utc


class UnsupportedIntent(ValueError):
    """Raised when the fixture gateway has no declared interpretation."""


class ModelGateway(Protocol):
    def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft:
        """Return a proposal-only structured interpretation."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class FixtureModelGateway:
    """Deterministic stand-in for a local model during the first milestone.

    The example phrase intentionally remains unresolved because “don't blast”
    does not specify a brightness or scene. This makes ambiguity visible to
    the approval boundary instead of turning model fluency into authority.
    """

    _EXAMPLE = "when i'm working late, don't blast the bedroom lights when i walk in."

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.replace("’", "'").casefold().split())

    def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft:
        require_aware_utc(now, name="interpretation time")
        if self._normalize(text).rstrip(".") != self._EXAMPLE.rstrip("."):
            raise UnsupportedIntent("the fixture gateway has no declared interpretation for this text")
        return RuleDraft(
            draft_id=_new_id("draft"),
            household_id=principal.household_id,
            proposed_by=principal.actor_id,
            source_text=text,
            interpretation=(
                "When the requester enters the bedroom while the household "
                "context working_late is active, apply a gentler bedroom-light setting."
            ),
            trigger_person_id=principal.actor_id,
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


__all__ = ["FixtureModelGateway", "ModelGateway", "UnsupportedIntent"]
