"""Sync: how a scope's records leave this machine, if ever.

Contract-only package (see `contracts.py`'s docstring) -- no implementation
of `SyncProvider` yet, local or otherwise.
"""

from .contracts import SyncBatch, SyncProvider, SyncReceipt, SyncRecord

__all__ = ["SyncBatch", "SyncProvider", "SyncReceipt", "SyncRecord"]
