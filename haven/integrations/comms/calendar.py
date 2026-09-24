"""Calendar provider contracts + a stdlib ICS-file adapter (spec page 32).

External calendars remain authoritative: the local ICS adapter treats the
file as the authority and every governed mutation is verified by re-read.
Read and mutate are separate capabilities -- a connected calendar grants
reading; create/update/delete cross the governed path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

ICS_PROVIDER_ID = "haven.calendar.ics"

_CONTENT_LINES = ("SUMMARY", "DTSTART", "DTEND", "LOCATION", "UID", "URL", "DESCRIPTION")


@dataclass(frozen=True)
class CalendarEvent:
    """One calendar event. `event_id` is the stable ICS UID."""

    event_id: str
    title: str
    start_at: datetime
    end_at: datetime | None = None
    attendees: tuple[str, ...] = ()
    location: str = ""
    provider_id: str = ICS_PROVIDER_ID
    source_path: str = ""

    def __post_init__(self) -> None:
        for field_name in ("event_id", "title"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("start_at", "end_at"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        if not isinstance(self.attendees, tuple):
            object.__setattr__(self, "attendees", tuple(self.attendees))


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _parse_dt(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _parse_ics_value(name: str, params: str, value: str):
    if name in ("DTSTART", "DTEND"):
        return _parse_dt(value)
    return value.strip()


def parse_ics(text: str, *, source_path: str = "") -> tuple[CalendarEvent, ...]:
    """Parse VEVENTs from iCalendar text with the stdlib only."""

    events: list[CalendarEvent] = []
    current: dict | None = None
    attendees: list[str] = []
    for line in _unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            attendees = []
            continue
        if upper == "END:VEVENT":
            if current is not None and current.get("UID") and current.get("SUMMARY"):
                events.append(
                    CalendarEvent(
                        event_id=str(current["UID"]),
                        title=str(current["SUMMARY"]),
                        start_at=current.get("DTSTART"),
                        end_at=current.get("DTEND"),
                        attendees=tuple(attendees),
                        location=str(current.get("LOCATION", "")),
                        source_path=source_path,
                    )
                )
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name_params, value = line.split(":", 1)
        name = name_params.split(";", 1)[0].upper()
        if name == "ATTENDEE":
            attendees.append(value.removeprefix("mailto:").strip())
        elif name in _CONTENT_LINES:
            current[name] = _parse_ics_value(name, name_params, value)
    return tuple(events)


def render_ics(events) -> str:
    """Serialize events back to minimal iCalendar (the adapter's write path)."""

    def fmt(dt: datetime) -> str:
        return dt.astimezone(datetime.now().astimezone().tzinfo).strftime("%Y%m%dT%H%M%S")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//HAVEN//local ics//EN"]
    for event in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event.event_id}")
        lines.append(f"SUMMARY:{event.title}")
        lines.append(f"DTSTART:{fmt(event.start_at)}")
        if event.end_at is not None:
            lines.append(f"DTEND:{fmt(event.end_at)}")
        if event.location:
            lines.append(f"LOCATION:{event.location}")
        for attendee in event.attendees:
            lines.append(f"ATTENDEE:mailto:{attendee}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"


class LocalIcsCalendarProvider:
    """Read + governed-write adapter over explicit user-supplied .ics files."""

    provider_id = ICS_PROVIDER_ID

    def __init__(self, paths) -> None:
        self._paths = [Path(path) for path in paths]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(str(path) for path in self._paths)

    def events(self) -> tuple[CalendarEvent, ...]:
        events: list[CalendarEvent] = []
        for path in self._paths:
            if not path.is_file():
                continue
            try:
                events.extend(parse_ics(path.read_text(encoding="utf-8"), source_path=str(path)))
            except OSError:
                continue
        return tuple(sorted(events, key=lambda event: (event.start_at, event.event_id)))

    def get(self, event_id: str) -> CalendarEvent | None:
        return next((event for event in self.events() if event.event_id == event_id), None)

    def _primary_path(self) -> Path | None:
        return self._paths[0] if self._paths else None

    def create_event(self, event: CalendarEvent) -> CalendarEvent | None:
        path = self._primary_path()
        if path is None:
            return None
        existing = list(self.events())
        if any(item.event_id == event.event_id for item in existing):
            return None
        existing.append(replace(event, source_path=str(path)))
        try:
            path.write_text(render_ics(existing), encoding="utf-8")
        except OSError:
            return None
        return self.get(event.event_id)

    def update_event(self, event: CalendarEvent) -> CalendarEvent | None:
        existing = list(self.events())
        index = next(
            (i for i, item in enumerate(existing) if item.event_id == event.event_id), None
        )
        if index is None:
            return None
        path = Path(existing[index].source_path) if existing[index].source_path else self._primary_path()
        if path is None:
            return None
        existing[index] = replace(event, source_path=str(path))
        try:
            path.write_text(render_ics(existing), encoding="utf-8")
        except OSError:
            return None
        return self.get(event.event_id)

    def delete_event(self, event_id: str) -> bool:
        existing = list(self.events())
        remaining = [item for item in existing if item.event_id != event_id]
        if len(remaining) == len(existing):
            return False
        touched = {item.source_path for item in existing if item.event_id == event_id}
        for source_path in touched:
            path = Path(source_path)
            try:
                path.write_text(render_ics([item for item in remaining if item.source_path == source_path]), encoding="utf-8")
            except OSError:
                return False
        return True


__all__ = [
    "CalendarEvent",
    "ICS_PROVIDER_ID",
    "LocalIcsCalendarProvider",
    "parse_ics",
    "render_ics",
]
