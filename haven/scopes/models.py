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

from dataclasses import dataclass


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ScopeRef:
    """One scope: a named partition of resources, claims, and authority.

    `parent_scope_id` lets scopes nest (a `project` inside a `workspace`
    inside an `organization`) without this type knowing what nesting means
    for authority or visibility -- that is a future policy layer's job, not
    this record's.
    """

    scope_id: str
    kind: str
    name: str
    parent_scope_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        object.__setattr__(self, "name", _require_text(self.name, name="name"))
        if self.parent_scope_id is not None:
            object.__setattr__(
                self, "parent_scope_id", _require_text(self.parent_scope_id, name="parent_scope_id")
            )


__all__ = ["ScopeRef"]
