"""Scopes: the partition unit the life substrate keys against.

Contract-only package (see `models.py`'s docstring) -- no registry, no
policy layer, no persistence yet.
"""

from .models import ScopeRef

__all__ = ["ScopeRef"]
