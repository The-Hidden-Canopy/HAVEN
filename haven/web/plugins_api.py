"""Plugin Marketplace API payloads: PluginManager results -> wire shapes.

Mirrors `models_api.py`: handlers stay thin, this module turns
`PluginMarketplaceView`/entries into the ok-envelope JSON the UI consumes.
Operational failures are reported as ``{"ok": false, "error": ...}`` with
HTTP 200 -- never a traceback, and never a partially-verified catalog
entry: everything here already passed both the Hub's signature and
HAVEN's own independent boundary re-check in `catalog_client.py`.
"""

from __future__ import annotations

from typing import Any

from ..plugins import PluginManager, PluginMarketplaceEntry, PluginMarketplaceView


def plugin_row(entry: PluginMarketplaceEntry) -> dict[str, Any]:
    descriptor = entry.descriptor
    return {
        "plugin_id": descriptor.plugin_id,
        "display_name": descriptor.display_name,
        "publisher": descriptor.publisher,
        "capability": descriptor.capability.value,
        "status": descriptor.status.value,
        "data_boundary": descriptor.data_boundary.value,
        "description": descriptor.description,
        "enabled": entry.enabled,
    }


def marketplace_payload(view: PluginMarketplaceView) -> dict[str, Any]:
    return {
        "ok": True,
        "plugins": [plugin_row(entry) for entry in view.entries],
        "catalog_version": view.catalog_version,
        "fetched_at": view.fetched_at.isoformat() if view.fetched_at is not None else None,
        "catalog_error": view.catalog_error,
    }


def refresh_payload(manager: PluginManager) -> dict[str, Any]:
    return marketplace_payload(manager.refresh_catalog())


def current_payload(manager: PluginManager) -> dict[str, Any]:
    return marketplace_payload(manager.view())


__all__ = [
    "current_payload",
    "marketplace_payload",
    "plugin_row",
    "refresh_payload",
]
