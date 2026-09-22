"""HAVEN's plugin marketplace: catalog client, local enablement, manager.

See `docs/plugin-boundary.md` for the contract this package enforces. In
one sentence: a plugin never runs inside HAVEN and never receives anything
beyond a household's explicitly exported receipt stream, relayed through
the Hub's signed catalog -- this package is the household-side half of
that contract (fetch + verify the catalog, record opt-in/opt-out), not the
export or relay themselves.
"""

from __future__ import annotations

from .catalog_client import CatalogFetchError, CatalogSnapshot, DEFAULT_CATALOG_URL, fetch_catalog
from .contracts import (
    InvalidPluginIdError,
    PluginCapability,
    PluginDataBoundary,
    PluginDescriptor,
    PluginStatus,
    safe_plugin_id,
)
from .manager import PluginManager, PluginMarketplaceEntry, PluginMarketplaceView, UnknownPluginError
from .registry import PluginEnablement, PluginRegistry, PluginRegistryError

__all__ = [
    "CatalogFetchError",
    "CatalogSnapshot",
    "DEFAULT_CATALOG_URL",
    "InvalidPluginIdError",
    "PluginCapability",
    "PluginDataBoundary",
    "PluginDescriptor",
    "PluginEnablement",
    "PluginManager",
    "PluginMarketplaceEntry",
    "PluginMarketplaceView",
    "PluginRegistry",
    "PluginRegistryError",
    "PluginStatus",
    "UnknownPluginError",
    "fetch_catalog",
    "safe_plugin_id",
]
