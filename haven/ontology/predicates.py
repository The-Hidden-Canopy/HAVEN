"""The HAVEN Base Ontology's predicate vocabulary -- small, public, extensible.

Same discipline as `haven.ontology.concepts`: namespaced strings, not a
closed enum. `"git:parent_commit"` or `"home:reports_state"` are exactly as
valid as anything below; `BASE_PREDICATES` documents what this repo ships,
it does not gate what `haven.ontology.assertions.OntologyAssertion.predicate`
will accept.
"""

from __future__ import annotations

BELONGS_TO = "haven:belongs_to"
CONTAINS = "haven:contains"
CREATED_BY = "haven:created_by"
OWNED_BY = "haven:owned_by"
ASSIGNED_TO = "haven:assigned_to"
RELATED_TO = "haven:related_to"
DERIVED_FROM = "haven:derived_from"
SUPPORTS = "haven:supports"
CONTRADICTS = "haven:contradicts"
SUPERSEDES = "haven:supersedes"
REFERENCES = "haven:references"
LOCATED_IN = "haven:located_in"
EXECUTED_BY = "haven:executed_by"
PRODUCED = "haven:produced"
DEPENDS_ON = "haven:depends_on"
VISIBLE_TO = "haven:visible_to"
SHARED_WITH = "haven:shared_with"

BASE_PREDICATES = frozenset(
    {
        BELONGS_TO,
        CONTAINS,
        CREATED_BY,
        OWNED_BY,
        ASSIGNED_TO,
        RELATED_TO,
        DERIVED_FROM,
        SUPPORTS,
        CONTRADICTS,
        SUPERSEDES,
        REFERENCES,
        LOCATED_IN,
        EXECUTED_BY,
        PRODUCED,
        DEPENDS_ON,
        VISIBLE_TO,
        SHARED_WITH,
    }
)

__all__ = [
    "ASSIGNED_TO",
    "BASE_PREDICATES",
    "BELONGS_TO",
    "CONTAINS",
    "CONTRADICTS",
    "CREATED_BY",
    "DEPENDS_ON",
    "DERIVED_FROM",
    "EXECUTED_BY",
    "LOCATED_IN",
    "OWNED_BY",
    "PRODUCED",
    "REFERENCES",
    "RELATED_TO",
    "SHARED_WITH",
    "SUPERSEDES",
    "SUPPORTS",
    "VISIBLE_TO",
]
