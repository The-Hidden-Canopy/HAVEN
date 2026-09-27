"""Credentials as a subsystem, not provider config fields (native product-
consolidation plan, P1 + §16's locked-in decision). A provider secret lives
here -- encrypted via Windows DPAPI, never in plain JSON, logs, receipts, or
model context -- referenced elsewhere only by its `credential_id`.
"""

from .store import CredentialKind, CredentialMetadata, CredentialStore, UnknownCredentialError

__all__ = ["CredentialKind", "CredentialMetadata", "CredentialStore", "UnknownCredentialError"]
