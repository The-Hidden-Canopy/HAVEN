"""Deterministic, household-grounded intent classification.

The demo used to own a private regex/keyword intent table. That table moved
here: `DeterministicIntentInterpreter` turns one utterance into exactly ONE
`Intent` form (see `haven.intelligence.intents`), grounded against a bounded
`WorldView` when one is supplied and against a room focus when one is set.
The interpreter is pure -- no I/O, no store, no registry -- and proposes
nothing executable on its own: an `ActionProposal` still crosses authority
before any device command exists.

Classification order mirrors the historic demo behavior:

1. "when ..." automation phrasing (no trailing "?") -> a `RuleDraft`, drafted
   by the caller-supplied `draft_for(text, principal, now)` callable. When
   the callable declines (the scripted fixture only drafts its example
   phrase), the honest answer is a `ConversationMessage`, which the demo
   renders as "I can't do that yet."
2. One-shot garage commands -> an `ActionProposal` for the garage door.
   "close ..." is only proposed when the world shows the door open, matching
   the demo's semantics; with no world to check, a targeted command becomes
   a `ClarificationRequest` instead of a guess.
3. One-shot light commands -> an `ActionProposal` with a
   `DeviceSelector(role="light", room=...)`, with the room grounded against
   the world's room names/ids ("the office" and "office" both resolve). An
   unresolvable room becomes a `ClarificationRequest` ("Which room do you
   mean?"); "turn on ..." is answered honestly with a
   `ConversationMessage`, because no `TURN_LIGHT_ON` action kind exists.
4. Question shapes -> `QueryRequest`.
5. Everything else -> `ConversationMessage`.

Rooms and device presence are read only from the `WorldView` -- projected,
bounded evidence -- never from a device registry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from haven.core.domain import ActionKind, DeviceSelector, Principal, RuleDraft
from haven.core.time import require_aware_utc

from .gateway import UnsupportedIntent
from .intents import (
    ActionProposal,
    ClarificationRequest,
    ConversationMessage,
    Intent,
    QueryRequest,
)
from .worldview import WorldView

# A draft callable proposes a RuleDraft for automation phrasing, or declines
# by raising UnsupportedIntent; anything it cannot draft is conversation.
DraftFor = Callable[[str, Principal, datetime], RuleDraft]

_QUESTION_LEADERS = ("is", "are", "does", "do", "did", "what", "why", "how", "who", "where", "which", "can")

_GARAGE_CLOSE_PHRASES = ("close the garage", "close the garage door")
_GARAGE_OPEN_PHRASES = ("open the garage", "open the garage door")
_GARAGE_DEVICE_ID = "garage_door"

_LIGHT_OFF_ROOM_PATTERNS = (
    "turn off the {room} light",
    "turn the {room} light off",
    "turn off the light in the {room}",
)
_LIGHT_ON_ROOM_PATTERNS = (
    "turn on the {room} light",
    "turn the {room} light on",
    "turn on the light in the {room}",
)
_LIGHT_OFF_BARE_PATTERNS = (
    "turn that light off",
    "turn off the light",
    "turn off that light",
    "turn the light off",
)
_LIGHT_ON_BARE_PATTERNS = (
    "turn that light on",
    "turn on the light",
    "turn on that light",
    "turn the light on",
)

_CLARIFY_ROOM_QUESTION = "Which room do you mean?"
_TURN_ON_GAP_NOTE = "I can't turn lights on yet."


def _normalize(text: str) -> str:
    return " ".join(text.replace("’", "'").casefold().split())


class DeterministicIntentInterpreter:
    """The zero-config, rule-based classifier behind the fixture provider.

    `draft_for` is the bridge from classification back to rule drafting: it
    receives the raw text, the acting principal, and the decision time, and
    returns a `RuleDraft` or raises `UnsupportedIntent`. `principal` and
    `now` are stored so `classify` can honor that three-argument contract
    while keeping its own signature to evidence inputs only.
    """

    def __init__(
        self,
        *,
        draft_for: DraftFor | None = None,
        principal: Principal | None = None,
        now: datetime | None = None,
    ) -> None:
        if draft_for is not None and not callable(draft_for):
            raise ValueError("draft_for must be callable")
        if draft_for is not None and (principal is None or now is None):
            raise ValueError("principal and now are required when draft_for is set")
        if now is not None:
            require_aware_utc(now, name="interpretation time")
        self._draft_for = draft_for
        self._principal = principal
        self._now = now

    def classify(self, text: str, *, world: WorldView | None, room_focus: str | None) -> Intent:
        """Propose exactly one intent form for `text`.

        `world` grounds rooms and device presence; `room_focus` resolves
        bare commands like "turn that light off". Without either, targeted
        commands honestly become clarifications instead of guesses.
        """

        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if world is not None and not isinstance(world, WorldView):
            raise ValueError("world must be a WorldView or None")
        if room_focus is not None and not room_focus.strip():
            raise ValueError("room_focus must be a non-empty string when set")
        ends_with_question = text.strip().endswith("?")
        normalized = _normalize(text).rstrip(".!?")

        if self._is_rule_phrasing(normalized, ends_with_question):
            return self._draft_rule(text)
        proposal = self._classify_action(normalized, text=text, world=world, room_focus=room_focus)
        if proposal is not None:
            return proposal
        if self._is_question(normalized, ends_with_question):
            return QueryRequest(text=text)
        if self._matches_any(normalized, _LIGHT_ON_ROOM_PATTERNS + _LIGHT_ON_BARE_PATTERNS):
            # No TURN_LIGHT_ON action kind exists; say so instead of drafting.
            return ConversationMessage(text=_TURN_ON_GAP_NOTE)
        return ConversationMessage(text=text)

    # -- automation phrasing --------------------------------------------------

    @staticmethod
    def _is_rule_phrasing(normalized: str, ends_with_question: bool) -> bool:
        return normalized.startswith("when ") and not ends_with_question

    def _draft_rule(self, text: str) -> Intent:
        if self._draft_for is not None:
            try:
                return self._draft_for(text, self._principal, self._now)
            except UnsupportedIntent:
                pass  # the drafter declined; fall through to an honest reply
        return ConversationMessage(text=text)

    # -- one-shot commands ----------------------------------------------------

    def _classify_action(
        self,
        normalized: str,
        *,
        text: str,
        world: WorldView | None,
        room_focus: str | None,
    ) -> ActionProposal | None:
        if normalized in _GARAGE_CLOSE_PHRASES:
            return self._garage_close(text=text, world=world)
        if normalized in _GARAGE_OPEN_PHRASES:
            return self._garage_open(text=text, world=world)
        room_phrase = self._room_from_patterns(normalized, _LIGHT_OFF_ROOM_PATTERNS)
        if room_phrase is not None:
            return self._light_off(text=text, room_phrase=room_phrase, world=world, room_focus=None)
        if any(normalized == pattern for pattern in _LIGHT_OFF_BARE_PATTERNS):
            return self._light_off(text=text, room_phrase=None, world=world, room_focus=room_focus)
        return None

    def _garage_close(self, *, text: str, world: WorldView | None) -> Intent:
        if world is None:
            return ClarificationRequest(question=_CLARIFY_ROOM_QUESTION, source_text=text)
        if self._garage_is_open(world):
            return ActionProposal(
                action_kind=ActionKind.CLOSE_GARAGE,
                target_device_id=_GARAGE_DEVICE_ID,
                target_selector=None,
                parameters=(),
                justification=f"direct command: {text}",
                source_text=text,
            )
        return ConversationMessage(text=text)

    def _garage_open(self, *, text: str, world: WorldView | None) -> Intent:
        if world is None:
            return ClarificationRequest(question=_CLARIFY_ROOM_QUESTION, source_text=text)
        return ActionProposal(
            action_kind=ActionKind.OPEN_GARAGE,
            target_device_id=_GARAGE_DEVICE_ID,
            target_selector=None,
            parameters=(),
            justification=f"direct command: {text}",
            source_text=text,
        )

    def _light_off(
        self,
        *,
        text: str,
        room_phrase: str | None,
        world: WorldView | None,
        room_focus: str | None,
    ) -> Intent:
        if room_phrase is not None:
            # An explicit room phrase is only as good as the evidence that
            # grounds it: no world, no resolution.
            room = self._ground_room(room_phrase, world)
            if room is None:
                return ClarificationRequest(question=_CLARIFY_ROOM_QUESTION, source_text=text)
        else:
            if room_focus is None:
                return ClarificationRequest(question=_CLARIFY_ROOM_QUESTION, source_text=text)
            # A bare command resolves against the focus; the world, when
            # present, confirms the focus is a real room.
            room = self._ground_room(room_focus, world) if world is not None else room_focus
            if room is None:
                return ClarificationRequest(question=_CLARIFY_ROOM_QUESTION, source_text=text)
        if world is not None and not self._room_has_light(world, room):
            # The room resolves but shows no light; the caller answers from
            # its own view ("I don't see a light in the ...").
            return ConversationMessage(text=text)
        return ActionProposal(
            action_kind=ActionKind.TURN_LIGHT_OFF,
            target_device_id=None,
            target_selector=DeviceSelector(role="light", room=room),
            parameters=(),
            justification=f"direct command: {text}",
            source_text=text,
        )

    # -- world grounding ------------------------------------------------------

    @staticmethod
    def _ground_room(room_phrase: str, world: WorldView | None) -> str | None:
        candidate = room_phrase.strip()
        if candidate.startswith("the "):
            candidate = candidate[len("the "):]
        if not candidate or world is None:
            return None
        normalized = candidate.replace("_", " ").casefold()
        for device in world.devices:
            room_id = device.room_id
            if room_id is not None and room_id.replace("_", " ").casefold() == normalized:
                return room_id
        for device in world.devices:
            if device.room_name is not None and device.room_name.casefold() == normalized:
                return device.room_id
        return None

    @staticmethod
    def _room_has_light(world: WorldView, room: str) -> bool:
        return any(device.kind == "light" and device.room_id == room for device in world.devices)

    @staticmethod
    def _garage_is_open(world: WorldView) -> bool:
        device = next((item for item in world.devices if item.device_id == _GARAGE_DEVICE_ID), None)
        if device is None:
            device = next((item for item in world.devices if item.kind == "cover"), None)
        return device is not None and device.is_on is True

    # -- questions ------------------------------------------------------------

    @staticmethod
    def _is_question(normalized: str, ends_with_question: bool) -> bool:
        if ends_with_question:
            return True
        return any(normalized.startswith(leader + " ") for leader in _QUESTION_LEADERS)

    @staticmethod
    def _matches_any(normalized: str, patterns: tuple[str, ...]) -> bool:
        for pattern in patterns:
            prefix, _, suffix = pattern.partition("{room}")
            if "{room}" in pattern:
                if normalized.startswith(prefix) and normalized.endswith(suffix):
                    return True
            elif normalized == pattern:
                return True
        return False

    @staticmethod
    def _room_from_patterns(normalized: str, patterns: tuple[str, ...]) -> str | None:
        for pattern in patterns:
            prefix, _, suffix = pattern.partition("{room}")
            if normalized.startswith(prefix) and normalized.endswith(suffix):
                end = len(normalized) - len(suffix) if suffix else len(normalized)
                room = normalized[len(prefix):end].strip()
                if room:
                    return room
        return None


__all__ = [
    "DeterministicIntentInterpreter",
    "DraftFor",
]
