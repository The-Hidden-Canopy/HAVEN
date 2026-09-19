"""Knowledge: believed propositions with provenance and a belief state.

`claims.py` is the contract (no admission policy yet -- see its own
docstring); `store.py` is its SQLite-backed persistence.
"""

from .claims import Claim, ClaimState, is_stale
from .store import ClaimStore

__all__ = ["Claim", "ClaimState", "ClaimStore", "is_stale"]
