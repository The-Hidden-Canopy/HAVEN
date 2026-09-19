"""`OntologyAssertion`: one `subject -> predicate -> object` edge, sourced.

Contract only -- no registry, no resolver, no traversal, no persistence
yet. This is the record the "life search bar" would eventually walk to
turn "find the NASA stuff" into a traversal instead of a keyword match
(`proposal-v7 belongs_to project:nasa-livei`, `project:nasa-livei
has_member person:bryan`, ...) -- see `haven.ontology.concepts`/
`predicates` for the base vocabulary `subject`/`predicate`/`object` draw
from, though this record does not enforce membership in that vocabulary
(a provider's own namespaced predicate is just as valid).

The important discipline, matching this repo's evidence philosophy
everywhere else: an assertion is not automatically a fact. `state` (reusing
`haven.knowledge.claims.ClaimState` rather than inventing a second, nearly
identical belief-state enum) says how the assertion came to be believed,
`confidence` says how sure, and `source_ref` says where it came from --
three separate questions a resolver would need to weigh, exactly the way
`AuthorityEngine` already treats "was this observed, and how fresh, and how
confident" as three separate checks rather than one boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from haven.core.time import require_aware_utc
from haven.knowledge.claims import ClaimState


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class OntologyAssertion:
    """One sourced, stateful edge in the household's relationship graph."""

    assertion_id: str
    subject: str
    predicate: str
    object: str
    scope_id: str
    state: ClaimState
    created_at: datetime
    source_ref: str | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "assertion_id", _require_text(self.assertion_id, name="assertion_id"))
        object.__setattr__(self, "subject", _require_text(self.subject, name="subject"))
        object.__setattr__(self, "predicate", _require_text(self.predicate, name="predicate"))
        # The dataclass field is named `object`, matching the
        # subject/predicate/object triple's own vocabulary; `self.object`
        # here is that field, never the builtin.
        object.__setattr__(self, "object", _require_text(self.object, name="object"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        if not isinstance(self.state, ClaimState):
            raise ValueError("state must be a ClaimState")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        if self.source_ref is not None:
            object.__setattr__(self, "source_ref", _require_text(self.source_ref, name="source_ref"))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


__all__ = ["OntologyAssertion"]
