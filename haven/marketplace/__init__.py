"""Curated HAVEN Marketplace discovery, kept separate from extensions.

The marketplace is a read-only discovery surface in this slice. It describes
products and optional HAVEN relationships; it does not install software,
execute launch commands, or grant authority. The existing ``haven.plugins``
package remains the narrow Export Consumer catalog.
"""

from .cache import MarketplaceCache, MarketplaceCacheError
from .catalog_client import (
    CatalogFetchError,
    MarketplaceCatalogClient,
    MarketplaceCatalogSnapshot,
    MarketplaceView,
    fetch_catalog,
    parse_and_verify_catalog,
)
from .contracts import (
    MarketplaceDistribution,
    MarketplaceDistributionKind,
    MarketplaceExecution,
    MarketplaceItem,
    MarketplaceKind,
    MarketplaceReleaseState,
    MarketplaceRelationship,
    safe_product_id,
)

__all__ = [
    "CatalogFetchError",
    "MarketplaceCache",
    "MarketplaceCacheError",
    "MarketplaceCatalogClient",
    "MarketplaceCatalogSnapshot",
    "MarketplaceDistribution",
    "MarketplaceDistributionKind",
    "MarketplaceExecution",
    "MarketplaceItem",
    "MarketplaceKind",
    "MarketplaceReleaseState",
    "MarketplaceRelationship",
    "MarketplaceView",
    "fetch_catalog",
    "parse_and_verify_catalog",
    "safe_product_id",
]
