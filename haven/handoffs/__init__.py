"""Handoffs: a portable, referenced bundle of context between scopes.

Contract-only package (see `models.py`'s docstring) -- no builder, no
importer, no redaction logic yet.
"""

from .models import HandoffPackage

__all__ = ["HandoffPackage"]
