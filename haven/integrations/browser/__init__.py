"""Browser context integration (spec page 31).

HAVEN observes tab identity/title/url/activity from explicitly connected
browsers through the `BrowserHub` connector seam; it never indexes page
bodies, cookies, or history, and incognito tabs are excluded at ingest.
"""

from .provider import (
    PROVIDER_ID,
    BrowserCommandResult,
    BrowserHub,
    BrowserObservationProvider,
    BrowserTabSnapshot,
    domain_of,
    tab_resource_id,
)

__all__ = [
    "PROVIDER_ID",
    "BrowserCommandResult",
    "BrowserHub",
    "BrowserObservationProvider",
    "BrowserTabSnapshot",
    "domain_of",
    "tab_resource_id",
]
