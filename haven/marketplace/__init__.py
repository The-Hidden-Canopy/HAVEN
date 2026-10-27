"""Curated product discovery and host-approved inert local content lifecycle.

Export Consumers, feature activation, and models retain separate contracts.
Product metadata never grants authority or supplies executable commands.
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
from .lifecycle import LocalProductManager, LocalRelease, ProductError
from .local_fixture import create_local_fixture_manager

__all__ = [
    "CatalogFetchError",
    "LocalProductManager",
    "LocalRelease",
    "ProductError",
    "create_local_fixture_manager",
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
