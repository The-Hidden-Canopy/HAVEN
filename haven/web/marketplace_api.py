"""Wire projections for the read-only HAVEN Marketplace discovery slice."""

from __future__ import annotations

from typing import Any

from ..marketplace import MarketplaceCatalogClient, MarketplaceItem, MarketplaceView


def marketplace_item_row(item: MarketplaceItem) -> dict[str, Any]:
    """Project product metadata without exposing an install command or secret."""

    return {
        "product_id": item.product_id,
        "display_name": item.display_name,
        "publisher": item.publisher,
        "kind": item.kind.value,
        "categories": list(item.categories),
        "release_state": item.release_state.value,
        "summary": item.summary,
        "description": item.description,
        "platforms": list(item.platforms),
        "execution": item.execution.value if item.execution is not None else None,
        "license": item.license_display,
        "source": {
            "visibility": item.source_visibility,
            "url": item.source_url,
        },
        "haven_integration": {
            "available": item.haven_integration_available,
            "integration_ids": list(item.integration_ids),
        },
        "relationships": [
            {"type": relationship.relationship_type, "product_id": relationship.product_id}
            for relationship in item.relationships
        ],
        "has_distribution": item.has_distribution,
        # Phase 1 is discovery only. Keep this explicit so a future UI cannot
        # mistake a distribution pointer for permission to install it.
        "install_available": False,
    }


def marketplace_payload(
    view: MarketplaceView,
    *,
    items: tuple[MarketplaceItem, ...] | None = None,
) -> dict[str, Any]:
    selected = view.items if items is None else items
    return {
        "ok": True,
        "items": [marketplace_item_row(item) for item in selected],
        "catalog_version": view.catalog_version,
        "publisher": view.publisher,
        "issued_at": view.issued_at.isoformat() if view.issued_at is not None else None,
        "expires_at": view.expires_at.isoformat() if view.expires_at is not None else None,
        "source": view.source,
        "stale": view.stale,
        "signature_verified": view.signature_verified,
        "catalog_error": view.catalog_error,
        "install_available": False,
    }


def current_payload(client: MarketplaceCatalogClient, **filters: Any) -> dict[str, Any]:
    return marketplace_payload(client.view(), items=client.list_items(**filters))


def refresh_payload(client: MarketplaceCatalogClient) -> dict[str, Any]:
    return marketplace_payload(client.refresh())


__all__ = ["current_payload", "marketplace_item_row", "marketplace_payload", "refresh_payload"]
