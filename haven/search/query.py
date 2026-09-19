"""`SearchQuery`/`SearchHit`: the "life search bar"'s contract, not its engine.

Contract only -- no index, no ranking, no service yet. Deliberately shaped
so a real implementation is not required to be keyword search: `text` is
one input among several a ranking pass could combine with recency,
`haven.ontology` relationships, `haven.knowledge` claims, resource type
filters, and scope membership -- optional embeddings are ranking machinery
that could plug into that combination later, never the architecture itself.
A query without a scope filter is a query across every scope the caller can
see; enforcing "can see" is a future authority concern, not this record's.
"""

from __future__ import annotations

from dataclasses import dataclass


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class SearchQuery:
    text: str
    scope_ids: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()
    limit: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_text(self.text, name="text"))
        object.__setattr__(self, "scope_ids", tuple(self.scope_ids))
        object.__setattr__(self, "resource_types", tuple(self.resource_types))
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit <= 0:
            raise ValueError("limit must be a positive integer")


@dataclass(frozen=True)
class SearchHit:
    """One ranked result. `reason` is a short human-readable explanation
    ("matched title", "related via belongs_to project:haven") -- a ranking
    pass that cannot explain its own hit is a ranking pass a household
    cannot trust, the same transparency `AuthorityDecision` already gives
    every governed action."""

    resource_id: str
    score: float
    reason: str
    matched_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_id", _require_text(self.resource_id, name="resource_id"))
        object.__setattr__(self, "reason", _require_text(self.reason, name="reason"))
        object.__setattr__(self, "matched_refs", tuple(self.matched_refs))


__all__ = ["SearchHit", "SearchQuery"]
