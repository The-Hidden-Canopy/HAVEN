"""Ontology: subject -> predicate -> object edges, sourced and stateful.

`assertions.py` is the contract (no resolver or multi-hop traversal yet --
see its own docstring); `store.py` is its SQLite-backed persistence, with
`edges_from`/`edges_to` as the indexed lookup primitives a future resolver
would walk. `concepts`/`predicates` are the small, public,
namespace-extensible base vocabulary; nothing here enforces membership in
either.
"""

from .assertions import OntologyAssertion
from .store import OntologyStore

__all__ = ["OntologyAssertion", "OntologyStore"]
