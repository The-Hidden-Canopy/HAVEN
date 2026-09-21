"""Desktop hosting for HAVEN's compatibility and native clients.

The desktop layer owns process/window concerns only.  The compatibility
HTML/CSS/JS renderer remains available for browser mode, while the WinUI
client uses the local named-pipe adapter.  Python remains the runtime and
authority boundary for both.
"""

from .shell import (
    DesktopShell,
    DesktopShellAlreadyRunning,
    DesktopShellError,
    find_edge_executable,
)
from .native_host import NativeIpcHost

__all__ = [
    "DesktopShell",
    "DesktopShellAlreadyRunning",
    "DesktopShellError",
    "find_edge_executable",
    "NativeIpcHost",
]
