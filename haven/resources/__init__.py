"""Resources: the one shape every "thing" in a household's life is.

`models.py` is the contract (no discovery wiring yet -- see its own
docstring); `store.py` is its SQLite-backed persistence.
"""

from .models import ResourceRecord
from .store import ResourceStore

__all__ = ["ResourceRecord", "ResourceStore"]
