"""Conservative, side-effect-free parsing for authoring phrases.

The normal intelligence path is intentionally not used for these first
declaration mutations.  A room or person declaration changes installation
configuration, so the parser only recognizes a small, unambiguous phrase
set and returns a :class:`MutationProposal`.  The web composition layer then
applies that proposal through ``SetupService``.
"""

from __future__ import annotations

import re

from haven.intelligence.intents import MutationProposal


_TRAILING_PUNCTUATION = ".!?"
_ROOM_CREATE = (
    re.compile(r"^(?:add|create)\s+(?:a|an|the)\s+room\s+(?:called|named)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:add|create)\s+room\s+(?:called|named)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:add|create)\s+(?:a|an|the)\s+(.+)$", re.IGNORECASE),
)
_ROOM_RENAME = re.compile(
    r"^(?:call|name)\s+(?:this\s+)?room\s+(?:the\s+)?(.+)$", re.IGNORECASE
)
_ROOM_DELETE = re.compile(r"^(?:remove|delete)\s+(?:the\s+)?(.+)$", re.IGNORECASE)
_PERSON_CREATE = re.compile(r"^(.+?)\s+lives\s+here(?:\s+too)?$", re.IGNORECASE)
_AUTOMATION_CREATE = re.compile(
    r"^(?:every\s+)?(?P<days>weekday|weekdays|weekend|weekends|day|daily|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+"
    r"(?P<time>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.)?)\s+"
    r"turn\s+off\s+(?P<room>.+?)\s+lights?$",
    re.IGNORECASE,
)
_AUTOMATION_REMOVE_DAY = re.compile(
    r"^(?:do\s+not|don't)\s+run\s+(?:that|the)\s+automation\s+on\s+"
    r"(?P<day>mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)$",
    re.IGNORECASE,
)

_WEEKDAY_NUMBERS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _clean_phrase(text: str) -> str:
    return " ".join(text.strip().split()).rstrip(_TRAILING_PUNCTUATION).strip()


def _label(value: str) -> str | None:
    value = _clean_phrase(value)
    if not value or len(value) > 120:
        return None
    return value


def _time_of_day(value: str) -> str | None:
    """Normalize the deliberately small conversational time vocabulary."""

    compact = " ".join(value.casefold().replace(".", "").split())
    suffix = ""
    if compact.endswith(" am") or compact.endswith(" pm"):
        compact, suffix = compact.rsplit(" ", 1)
    pieces = compact.split(":")
    if len(pieces) == 1:
        hour_text, minute_text = pieces[0], "00"
    elif len(pieces) == 2:
        hour_text, minute_text = pieces
    else:
        return None
    if not hour_text.isdigit() or not minute_text.isdigit():
        return None
    hour = int(hour_text)
    minute = int(minute_text)
    if suffix:
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            return None
        if suffix == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _weekdays(value: str) -> tuple[int, ...]:
    normalized = value.casefold()
    if normalized in {"day", "daily"}:
        return ()
    if normalized in {"weekday", "weekdays"}:
        return (0, 1, 2, 3, 4)
    if normalized in {"weekend", "weekends"}:
        return (5, 6)
    return (_WEEKDAY_NUMBERS[normalized],)


def _proposal(
    *,
    entity_kind: str,
    operation: str,
    source_text: str,
    attributes: tuple[tuple[str, str], ...],
    target_id: str | None = None,
) -> MutationProposal | None:
    try:
        return MutationProposal(
            entity_kind=entity_kind,
            operation=operation,
            attributes=attributes,
            source_text=source_text,
            target_id=target_id,
        )
    except ValueError:
        # The parser is a candidate generator.  An unrepresentable phrase is
        # simply left for the ordinary conversation/intelligence path.
        return None


def parse_authoring_intent(
    text: str,
    *,
    focus: str | None = None,
    automation_focus: str | None = None,
) -> MutationProposal | None:
    """Return a conservative declaration proposal, without mutating state.

    Supported examples are deliberately narrow for the first slice:

    * ``Add an office.``
    * ``Add a room called Workshop.``
    * ``Call this room the shop.`` (requires the current room focus)
    * ``Remove the old guest room.``
    * ``Bryan lives here too.``
    * ``Don't run that automation on Fridays.`` (requires an explicit
      automation focus)

    Only the two explicit scheduled-light forms above are parsed.  Richer
    automation language must continue through the existing RuleDraft ->
    authority -> approval lifecycle until it has the same explicit
    target/capability validation as the structured automation form.
    """

    if not isinstance(text, str) or not text.strip():
        return None
    source_text = text.strip()
    phrase = _clean_phrase(source_text)
    if not phrase:
        return None

    for pattern in _ROOM_CREATE:
        match = pattern.fullmatch(phrase)
        if match:
            name = _label(match.group(1))
            if name is not None:
                return _proposal(
                    entity_kind="room",
                    operation="create",
                    source_text=source_text,
                    attributes=(("name", name),),
                )
            return None

    match = _ROOM_RENAME.fullmatch(phrase)
    if match and isinstance(focus, str) and focus.strip():
        name = _label(match.group(1))
        if name is not None:
            return _proposal(
                entity_kind="room",
                operation="rename",
                target_id=focus.strip(),
                source_text=source_text,
                attributes=(("name", name),),
            )
        return None

    match = _ROOM_DELETE.fullmatch(phrase)
    if match and phrase.casefold().endswith(" room"):
        name = _label(match.group(1))
        if name is not None:
            return _proposal(
                entity_kind="room",
                operation="delete",
                source_text=source_text,
                attributes=(("name", name),),
            )

    match = _PERSON_CREATE.fullmatch(phrase)
    if match:
        name = _label(match.group(1))
        if name is not None:
            return _proposal(
                entity_kind="person",
                operation="create",
                source_text=source_text,
                attributes=(("name", name), ("role", "member")),
            )

    match = _AUTOMATION_CREATE.fullmatch(phrase)
    if match:
        time_of_day = _time_of_day(match.group("time"))
        room = _label(match.group("room"))
        if time_of_day is not None and room is not None:
            if room.casefold().startswith("the "):
                room = room[4:].strip()
            if room:
                return _proposal(
                    entity_kind="automation",
                    operation="create",
                    source_text=source_text,
                    attributes=(
                        ("action", "light.turn_off"),
                        ("room", room),
                        ("time_of_day", time_of_day),
                        ("weekdays", _weekdays(match.group("days"))),
                    ),
                )

    match = _AUTOMATION_REMOVE_DAY.fullmatch(phrase)
    if match and isinstance(automation_focus, str) and automation_focus.strip():
        day = match.group("day").casefold().removesuffix("s")
        return _proposal(
            entity_kind="automation",
            operation="update",
            target_id=automation_focus.strip(),
            source_text=source_text,
            attributes=(("remove_weekday", _WEEKDAY_NUMBERS[day]),),
        )
    return None


__all__ = ["parse_authoring_intent"]
