"""Lifecycle and failure states for a managed model.

Failure states stay explicit and are never collapsed to a generic "model
unavailable": HASH_MISMATCH means the bytes on disk are not the published
ones, BACKEND_MISSING means no loader plugin registered for the manifest's
backend, UNREACHABLE means an endpoint did not answer at registration time.
Each names a different remedy, so each is its own state.
"""

from enum import Enum


class ModelState(str, Enum):
    DISCOVERED = "discovered"
    INSPECTED = "inspected"
    REGISTERED = "registered"
    VERIFIED = "verified"
    READY = "ready"
    LOADED = "loaded"
    INCOMPLETE = "incomplete"
    UNSUPPORTED = "unsupported"
    HASH_MISMATCH = "hash_mismatch"
    LICENSE_UNKNOWN = "license_unknown"
    BACKEND_MISSING = "backend_missing"
    LOAD_FAILED = "load_failed"
    UNREACHABLE = "unreachable"


__all__ = ["ModelState"]
