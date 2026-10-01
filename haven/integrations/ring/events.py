"""Typed Ring evidence records.

These records describe an observed event; they are not an authorization
decision and carry no credential or media bytes. Metadata is intentionally a
small string map so a provider cannot smuggle a token or an unbounded vendor
payload into the core evidence path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping

from haven.core.domain import _require_confidence, _require_text
from haven.core.time import require_aware_utc


class RingEventKind(str, Enum):
    MOTION = "motion"
    DOORBELL = "doorbell"
    PERSON = "person"
    PACKAGE = "package"


def _metadata(value: Mapping[str, str] | tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    items = tuple(value.items()) if isinstance(value, Mapping) else tuple(value)
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, item in items:
        key = _require_text(key, name="Ring metadata key")
        if not isinstance(item, str):
            raise ValueError("Ring metadata values must be strings")
        if key in seen:
            raise ValueError(f"duplicate Ring metadata key: {key}")
        seen.add(key)
        normalized.append((key, item))
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class RingEvent:
    """One bounded, provenance-carrying Ring event."""

    event_id: str
    device_id: str
    kind: RingEventKind
    observed_at: datetime
    source: str = "ring.simulator"
    available: bool = True
    confidence: float = 1.0
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, name="Ring event_id"))
        object.__setattr__(self, "device_id", _require_text(self.device_id, name="Ring device_id"))
        if not isinstance(self.kind, RingEventKind):
            raise ValueError("Ring kind must be a RingEventKind")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="Ring observed_at"))
        object.__setattr__(self, "source", _require_text(self.source, name="Ring source"))
        _require_confidence(self.confidence, name="Ring confidence")
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "device_id": self.device_id,
            "kind": self.kind.value,
            "observed_at": self.observed_at.isoformat(),
            "source": self.source,
            "available": self.available,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }


__all__ = ["RingEvent", "RingEventKind"]
