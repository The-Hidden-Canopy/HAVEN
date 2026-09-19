"""`IdentityProvider`: who is acting, and what scopes they belong to.

Contract only -- no `LocalIdentityProvider` implementation yet. Reuses
`haven.core.domain.Principal` rather than inventing a second identity type:
a `Principal` is already `actor_id` + `household_id` + `role_tier`, and the
governed home loop's `AuthorityEngine` keeps reading exactly that,
unchanged. What is missing today is the wider question -- which *scopes*
(not just one household) a principal belongs to -- which is what
`ScopeMembership` adds alongside it, not instead of it. A private,
organizational identity provider (SSO, SCIM, multi-tenant auth) is exactly
one more `IdentityProvider` implementation; HAVEN Core depends only on this
Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from haven.core.domain import Principal
from haven.scopes.models import ScopeRef


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ScopeMembership:
    """One principal's standing in one scope -- separate from `RoleTier`,
    which is the governed home loop's own permission tier, not a general
    scope-membership role."""

    scope: ScopeRef
    actor_id: str
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_id", _require_text(self.actor_id, name="actor_id"))
        object.__setattr__(self, "role", _require_text(self.role, name="role"))


class IdentityProvider(Protocol):
    def current_principal(self) -> Principal: ...

    def memberships(self) -> tuple[ScopeMembership, ...]: ...


__all__ = ["IdentityProvider", "ScopeMembership"]
