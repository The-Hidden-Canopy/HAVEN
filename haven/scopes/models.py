"""`ScopeRef`: the unit HAVEN partitions everything else by.

Contract only -- no registry, no policy, no persistence yet. This is the
type the rest of the "life" substrate (resources, ontology assertions,
claims, handoffs) is keyed against instead of the household-only
`household_id` `haven.core.domain` still uses for the governed home loop.
That loop is untouched by this module; a household is simply expected to
become one `kind="household"` (or `"personal"`) scope once the two are
wired together, not before.

`kind` is deliberately an open vocabulary, the same discipline
`haven.providers.capabilities.ProviderCapabilities.kind` already uses for
provider kinds: HAVEN Core recognizes no fixed enum of scope kinds (a
household is not privileged over a `workspace` or a community-invented
kind), so nothing here forces `personal`/`project`/`workspace`/`team`/
`organization` as the only options -- those are just the vocabulary this
module's own docstring anticipates, not a closed set it enforces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ScopeRef:
    """One scope: a named partition of resources, claims, and authority.

    `parent_scope_id` lets scopes nest (a `project` inside a `workspace`
    inside an `organization`) without this type knowing what nesting means
    for authority or visibility -- hierarchy never implies inherited write
    authority; visibility and capability inheritance are explicit policy
    decisions made above this record (design spec pages 18-19).

    `created_at`, `status` and `policy_ref` are the persistence-minimum
    fields from the spec's scope record; they default so transient
    constructions (tests, proposal adapters) keep working.
    """

    scope_id: str
    kind: str
    name: str
    parent_scope_id: str | None = None
    created_at: datetime | None = None
    status: str = "active"
    policy_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        object.__setattr__(self, "name", _require_text(self.name, name="name"))
        if self.parent_scope_id is not None:
            object.__setattr__(
                self, "parent_scope_id", _require_text(self.parent_scope_id, name="parent_scope_id")
            )
        if self.created_at is not None and (
            self.created_at.tzinfo is None or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")
        object.__setattr__(self, "status", _require_text(self.status, name="status"))
        if self.policy_ref is not None:
            object.__setattr__(
                self, "policy_ref", _require_text(self.policy_ref, name="policy_ref")
            )


@dataclass(frozen=True)
class Membership:
    """One principal's standing in one scope: the persisted record.

    Roles and capabilities are scope-specific -- a principal can be an
    owner in their personal root and a plain member of the household
    child scope at the same time. `capabilities` is an open, scope-local
    vocabulary; an empty tuple means the membership grants presence
    (visibility) only. `valid_until` absent means the membership is
    open-ended.
    """

    principal_id: str
    scope_id: str
    role: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal_id", _require_text(self.principal_id, name="principal_id"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "role", _require_text(self.role, name="role"))
        if not isinstance(self.capabilities, tuple):
            object.__setattr__(self, "capabilities", tuple(self.capabilities))
        for capability in self.capabilities:
            _require_text(capability, name="capability")
        if self.valid_from is not None and (
            self.valid_from.tzinfo is None or self.valid_from.utcoffset() is None
        ):
            raise ValueError("valid_from must be timezone-aware")
        if self.valid_until is not None and (
            self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None
        ):
            raise ValueError("valid_until must be timezone-aware")

    def is_valid_at(self, now: datetime) -> bool:
        if self.valid_from is not None and now < self.valid_from:
            return False
        if self.valid_until is not None and now >= self.valid_until:
            return False
        return True


__all__ = ["Membership", "ScopeRef"]
