"""The first real "computer" resource provider: a confined local filesystem.

Not wired into any composition root by default -- like every other
integration in this package (`haven.integrations.bluetooth`,
`haven.integrations.ir`), a deployer registers it explicitly, with
household-declared allowed roots, never an inferred default.
"""

from .filesystem import (
    FILESYSTEM_ACTION_RISK,
    FilesystemProvider,
    FilesystemProviderConfig,
    PathOutsideAllowedRoots,
    PROVIDER_ID,
)
from .windows import (
    PROVIDER_ID as WINDOWS_PROVIDER_ID,
    WindowObservationProvider,
    WindowSnapshot,
    window_resource_id,
)

__all__ = [
    "FILESYSTEM_ACTION_RISK",
    "FilesystemProvider",
    "FilesystemProviderConfig",
    "PROVIDER_ID",
    "PathOutsideAllowedRoots",
    "WINDOWS_PROVIDER_ID",
    "WindowObservationProvider",
    "WindowSnapshot",
    "window_resource_id",
]
