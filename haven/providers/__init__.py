"""Pluggable provider boundary: capabilities in, licensing kept out.

Haven core asks a registry whether a capability is available; it never asks
which vendor or license tier backs it. Authentication and licensing belong
inside a provider plugin, not in this package.
"""

from .capabilities import CapabilityRegistry, ProviderCapabilities, UnknownProvider
from .defaults import build_default_registry

__all__ = [
    "CapabilityRegistry",
    "ProviderCapabilities",
    "UnknownProvider",
    "build_default_registry",
]
