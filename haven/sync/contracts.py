"""`SyncProvider`: how a scope's records leave this machine, if ever.

Contract only -- no `LocalOnlySyncProvider`/`FileExportSyncProvider`
implementation yet, and definitely no hosted implementation: this package
depends on nothing outside HAVEN Core, and nothing outside HAVEN Core is
depended on here. A private, hosted synchronization service is exactly one
more `SyncProvider` registered the same way any other provider plugs in
(`haven/providers/loader.py`) -- HAVEN never imports it, it imports HAVEN's
contract. That direction is the whole point: a household running zero
private services still has a complete, working `LocalOnlySyncProvider`-
shaped no-op available to it (a future implementation, not built here),
never a stub that only works once a subscription is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class SyncRecord:
    """One change to push: an opaque payload this module never interprets."""

    record_id: str
    kind: str
    payload: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _require_text(self.record_id, name="record_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        object.__setattr__(self, "payload", tuple(self.payload))


@dataclass(frozen=True)
class SyncReceipt:
    accepted: tuple[str, ...]
    rejected: tuple[str, ...]
    cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted", tuple(self.accepted))
        object.__setattr__(self, "rejected", tuple(self.rejected))


@dataclass(frozen=True)
class SyncBatch:
    records: tuple[SyncRecord, ...]
    cursor: str | None
    has_more: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))


class SyncProvider(Protocol):
    """Push/pull one scope's records against wherever this provider syncs to.

    `cursor` is opaque to the caller -- whatever a provider needs to resume
    a `pull` where the last one left off, the same "caller holds it, never
    interprets it" discipline a paginated API cursor already implies.
    """

    def push(self, *, scope_id: str, changes: tuple[SyncRecord, ...]) -> SyncReceipt: ...

    def pull(self, *, scope_id: str, cursor: str | None) -> SyncBatch: ...


__all__ = ["SyncBatch", "SyncProvider", "SyncReceipt", "SyncRecord"]
