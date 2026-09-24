"""Scopes: the partition unit the life substrate keys against.

`models.py` holds the contract records (`ScopeRef`, `Membership`);
`store.py` is their SQLite persistence and the derivation point for
visibility (design spec pages 18-19, milestone C).
"""

from .models import Membership, ScopeRef

__all__ = ["Membership", "ScopeRef"]
