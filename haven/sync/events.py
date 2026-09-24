"""Sync events and the per-type sync policy (spec pages 19/40).

Every synced mutation carries `origin_device_id`, `object_id`,
revision/causal parents, `scope_id`, and `event_id`. The allow-sync set is
an explicit table: user-authored projects, tasks, people, and claims sync.
Raw file content, model weights, provider credentials, and home secrets
do not -- a kind not in the table is never exported, and a pulled record
whose kind is not in the table is rejected on arrival. Fail closed both
directions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

# The explicit allow-sync table (spec: user-authored projects/tasks/people/
# claims sync). Anything else -- files, models, credentials, home secrets,
# devices, ontology assertions -- is excluded by omission, never by default.
SYNCABLE_KINDS = frozenset({"project", "task", "claim", "person"})

_DEVICE_FILENAME = "device_id.txt"

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class SyncEvent:
    """One mutation as it crosses the sync boundary."""

    event_id: str
    seq: int  # monotonic watermark within one installation's outbox
    origin_device_id: str
    object_id: str
    kind: str
    scope_id: str
    revision: int
    causal_parents: tuple[str, ...]
    payload: tuple[tuple[str, Any], ...]
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("event_id", "origin_device_id", "object_id", "kind", "scope_id"):
            _require_text(getattr(self, field_name), name=field_name)
        if self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(self.causal_parents, tuple):
            object.__setattr__(self, "causal_parents", tuple(self.causal_parents))
        if not isinstance(self.payload, tuple):
            object.__setattr__(self, "payload", tuple(self.payload))
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def wire(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "origin_device_id": self.origin_device_id,
            "object_id": self.object_id,
            "kind": self.kind,
            "scope_id": self.scope_id,
            "revision": self.revision,
            "causal_parents": list(self.causal_parents),
            "payload": dict(self.payload),
            "occurred_at": self.occurred_at.isoformat(),
        }

    @staticmethod
    def from_wire(payload: dict[str, Any]) -> "SyncEvent":
        if not isinstance(payload, dict):
            raise ValueError("sync event payload must be a mapping")
        return SyncEvent(
            event_id=str(payload["event_id"]),
            seq=int(payload["seq"]),
            origin_device_id=str(payload["origin_device_id"]),
            object_id=str(payload["object_id"]),
            kind=str(payload["kind"]),
            scope_id=str(payload["scope_id"]),
            revision=int(payload["revision"]),
            causal_parents=tuple(str(item) for item in payload.get("causal_parents", ())),
            payload=tuple((str(k), v) for k, v in dict(payload.get("payload", {})).items()),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
        )


def encode_event(event: SyncEvent) -> str:
    return json.dumps(event.wire(), sort_keys=True)


def decode_event(line: str) -> SyncEvent:
    return SyncEvent.from_wire(json.loads(line))


def device_id_for(data_dir) -> str:
    """This installation's stable origin device id (first run mints it)."""

    from pathlib import Path

    path = Path(data_dir) / _DEVICE_FILENAME
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    minted = f"device:{uuid4()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(minted, encoding="utf-8")
    return minted


def is_syncable(kind: str) -> bool:
    return kind in SYNCABLE_KINDS


__all__ = [
    "SYNCABLE_KINDS",
    "SyncEvent",
    "decode_event",
    "device_id_for",
    "encode_event",
    "is_syncable",
]
