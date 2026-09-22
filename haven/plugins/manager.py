"""Ties the Hub catalog client to local enable/disable state.

`PluginManager` is the one object the web API and CLI both call through. It
never talks to a plugin directly -- there is no such channel -- and it never
does anything with `enabled` beyond recording the household's choice:
turning that choice into an actual receipt-export relay is a separate,
not-yet-built integration surface (see `docs/plugin-boundary.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .catalog_client import CatalogFetchError, CatalogSnapshot, DEFAULT_CATALOG_URL, fetch_catalog
from .contracts import PluginDescriptor
from .registry import PluginRegistry


@dataclass(frozen=True)
class PluginMarketplaceEntry:
    """One catalog plugin, joined with this household's local choice."""

    descriptor: PluginDescriptor
    enabled: bool


@dataclass(frozen=True)
class PluginMarketplaceView:
    """What the marketplace has to show right now.

    `catalog_error` is a named `CatalogFetchError.code` (see
    `catalog_client.py`) when the Hub could not be reached, verified, or
    trusted -- entries is then whatever the last successful fetch produced
    (possibly empty), never a fabricated or partially-verified list.
    """

    entries: tuple[PluginMarketplaceEntry, ...]
    catalog_version: str | None
    fetched_at: datetime | None
    catalog_error: str | None


class PluginManager:
    def __init__(
        self,
        registry: PluginRegistry,
        *,
        catalog_url: str = DEFAULT_CATALOG_URL,
        clock=None,
    ) -> None:
        self._registry = registry
        self._catalog_url = catalog_url
        self._clock = clock
        self._last_snapshot: CatalogSnapshot | None = None
        self._last_error: str | None = None

    def refresh_catalog(self) -> PluginMarketplaceView:
        """Re-fetch the Hub catalog; failure keeps the last good snapshot."""

        try:
            self._last_snapshot = fetch_catalog(self._catalog_url)
            self._last_error = None
        except CatalogFetchError as exc:
            self._last_error = exc.code
        return self.view()

    def view(self) -> PluginMarketplaceView:
        snapshot = self._last_snapshot
        if snapshot is None:
            return PluginMarketplaceView(entries=(), catalog_version=None, fetched_at=None, catalog_error=self._last_error)
        entries = tuple(
            PluginMarketplaceEntry(descriptor=descriptor, enabled=self._registry.is_enabled(descriptor.plugin_id))
            for descriptor in snapshot.plugins
        )
        return PluginMarketplaceView(
            entries=entries,
            catalog_version=snapshot.catalog_version,
            fetched_at=snapshot.issued_at,
            catalog_error=self._last_error,
        )

    def _known_plugin_ids(self) -> set[str]:
        if self._last_snapshot is None:
            return set()
        return {descriptor.plugin_id for descriptor in self._last_snapshot.plugins}

    def enable(self, plugin_id: str) -> PluginMarketplaceView:
        """Opt a household into a plugin. Only a currently-cataloged id may be enabled.

        This never grants the plugin anything by itself -- it only records
        the household's choice. Nothing reads this flag yet to relay data;
        that relay is a separate integration surface, not built here.
        """

        if self._last_snapshot is None:
            raise UnknownPluginError("the plugin catalog has not been fetched yet")
        if plugin_id not in self._known_plugin_ids():
            raise UnknownPluginError(f"{plugin_id!r} is not in the current catalog")
        self._registry.set_enabled(plugin_id, True)
        return self.view()

    def disable(self, plugin_id: str) -> PluginMarketplaceView:
        self._registry.set_enabled(plugin_id, False)
        return self.view()


class UnknownPluginError(ValueError):
    """Raised when enabling a plugin id absent from the last-known catalog."""


__all__ = [
    "PluginManager",
    "PluginMarketplaceEntry",
    "PluginMarketplaceView",
    "UnknownPluginError",
]
