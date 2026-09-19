"""`ResourceRecord`: the one shape every "thing" in a household's life is.

Contract only -- no registry, no discovery wiring, no persistence yet. This
is deliberately not `haven.devices.DeviceManifest`: a device is a
resource this repo already actuates through a governed path
(`AuthorityEngine`/`ExecutionProviderRegistry`), and nothing here replaces
or migrates that. `ResourceRecord` is the wider shape a future computer/
filesystem/browser/calendar provider's *observations* take -- a file, a
repository, an open application tab, a calendar event, a task -- so the
"life search bar" has one record shape to search and relate regardless of
which provider produced it. Bridging a `DeviceManifest` into a
`ResourceRecord` (if that ever happens) is a future adapter's job, written
once both sides are real, not a concern this contract needs to resolve now.

`resource_type` is an open vocabulary (`file`, `document`, `repository`,
`application`, `browser_tab`, `task`, `calendar_event`, `device`,
`conversation`, `person`, `project`, ...), the same discipline
`haven.scopes.ScopeRef.kind` already uses -- this module enforces no closed
set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ResourceRecord:
    """One observed "thing" a provider can see, in the scope it belongs to.

    `locator` is whatever address the owning provider needs to reach the
    real thing again (a file path, a URL, a repository remote) -- HAVEN
    Core never interprets it, the same way it never interprets a device's
    `service` string. `content_hash` is optional because not every resource
    type has meaningful content to hash (a calendar event does not; a file
    does) -- present only when a provider can compute one, and the seam a
    future handoff/verification step would use to detect a resource
    changed underneath it. `metadata` is a tuple of pairs rather than a
    dict for the same immutability discipline every other HAVEN record
    uses, at the cost of a caller doing `dict(record.metadata)` to work
    with it as a mapping.
    """

    resource_id: str
    resource_type: str
    scope_id: str
    provider_id: str
    title: str
    locator: str | None
    capabilities: tuple[str, ...]
    observed_at: datetime
    content_hash: str | None = None
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_id", _require_text(self.resource_id, name="resource_id"))
        object.__setattr__(self, "resource_type", _require_text(self.resource_type, name="resource_type"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "provider_id", _require_text(self.provider_id, name="provider_id"))
        object.__setattr__(self, "title", _require_text(self.title, name="title"))
        if self.locator is not None:
            object.__setattr__(self, "locator", _require_text(self.locator, name="locator"))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        if self.content_hash is not None:
            object.__setattr__(self, "content_hash", _require_text(self.content_hash, name="content_hash"))
        object.__setattr__(self, "metadata", tuple(self.metadata))


__all__ = ["ResourceRecord"]
