"""Sync: how a scope's records leave this machine, if ever.

`contracts.py` is the Protocol; `events.py`/`store.py`/`transport.py`/
`engine.py` are the local implementation: a monotonic outbox, an explicit
per-type allow-set, a folder transport behind the two-method seam, and a
conflict state that never resolves silently.
"""

from .contracts import SyncBatch, SyncProvider, SyncReceipt, SyncRecord
from .engine import LocalSyncEngine
from .events import SYNCABLE_KINDS, SyncEvent, is_syncable
from .transport import FolderSyncTransport

__all__ = [
    "SYNCABLE_KINDS",
    "FolderSyncTransport",
    "LocalSyncEngine",
    "SyncBatch",
    "SyncEvent",
    "SyncProvider",
    "SyncReceipt",
    "SyncRecord",
    "is_syncable",
]
