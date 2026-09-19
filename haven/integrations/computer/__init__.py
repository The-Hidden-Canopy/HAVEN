"""The first real "computer" resource provider: a confined local filesystem.

Not wired into any composition root by default -- like every other
integration in this package (`haven.integrations.bluetooth`,
`haven.integrations.ir`), a deployer registers it explicitly, with
household-declared allowed roots, never an inferred default.
"""

from .filesystem import FilesystemProvider, FilesystemProviderConfig, PathOutsideAllowedRoots, PROVIDER_ID

__all__ = ["FilesystemProvider", "FilesystemProviderConfig", "PROVIDER_ID", "PathOutsideAllowedRoots"]
