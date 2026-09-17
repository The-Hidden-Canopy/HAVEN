"""The DiscoveryProvider contract, and a fixture for tests/demonstrations.

A real BLE scanner, mDNS/SSDP browser, or a vendor's own device registry
implements `discover()`; nothing here performs a real scan.
"""

from __future__ import annotations

from typing import Protocol

from .models import DiscoveredDevice


class DiscoveryProvider(Protocol):
    def discover(self) -> tuple[DiscoveredDevice, ...]:
        """Return the candidate devices this transport currently sees."""


class FixtureDiscoveryProvider:
    """Deterministic local provider used by tests and demonstrations."""

    def __init__(self, candidates: tuple[DiscoveredDevice, ...] = ()) -> None:
        self._candidates = tuple(candidates)

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        return self._candidates


__all__ = ["DiscoveryProvider", "FixtureDiscoveryProvider"]
