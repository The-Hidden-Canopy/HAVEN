"""`HandoffPackage`: a portable bundle of context, not "send this chat."

Contract only -- no builder, no importer, no redaction logic yet. A handoff
names *references* (`resource_refs`/`claim_refs`/`decision_refs`/
`task_refs`) rather than embedding every underlying resource, so exporting
one does not mean copying a household's entire file contents into a
package; a future builder decides per-reference whether the recipient
already has access to it (a REFERENCE handoff) or needs the content
exported alongside the package (a PORTABLE handoff) -- that distinction is
a builder-time decision this record does not encode itself, only the
result of it (a self-contained package either way, from the recipient's
point of view).

`unresolved` matters as much as what the package does carry: a handoff
that silently drops what its sender never got to is worse than one that
says so, the same "don't invent what evidence doesn't support" discipline
this repo already applies to a `RuleDraft`'s unresolved parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


_SCHEMA_VERSION = "haven-handoff-1"


@dataclass(frozen=True)
class HandoffPackage:
    schema_version: str
    handoff_id: str
    source_scope_id: str
    objective: str
    summary: str
    created_by: str
    created_at: datetime
    package_hash: str
    intended_target_scope: str | None = None
    resource_refs: tuple[str, ...] = ()
    claim_refs: tuple[str, ...] = ()
    decision_refs: tuple[str, ...] = ()
    task_refs: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"unsupported handoff schema_version: {self.schema_version!r}")
        object.__setattr__(self, "handoff_id", _require_text(self.handoff_id, name="handoff_id"))
        object.__setattr__(self, "source_scope_id", _require_text(self.source_scope_id, name="source_scope_id"))
        object.__setattr__(self, "objective", _require_text(self.objective, name="objective"))
        object.__setattr__(self, "summary", _require_text(self.summary, name="summary"))
        object.__setattr__(self, "created_by", _require_text(self.created_by, name="created_by"))
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        object.__setattr__(self, "package_hash", _require_text(self.package_hash, name="package_hash"))
        if self.intended_target_scope is not None:
            object.__setattr__(
                self, "intended_target_scope", _require_text(self.intended_target_scope, name="intended_target_scope")
            )
        object.__setattr__(self, "resource_refs", tuple(self.resource_refs))
        object.__setattr__(self, "claim_refs", tuple(self.claim_refs))
        object.__setattr__(self, "decision_refs", tuple(self.decision_refs))
        object.__setattr__(self, "task_refs", tuple(self.task_refs))
        object.__setattr__(self, "unresolved", tuple(self.unresolved))
        object.__setattr__(self, "permissions", tuple(self.permissions))


__all__ = ["HandoffPackage"]
