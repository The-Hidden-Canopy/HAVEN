"""Native desktop hosting for the existing HAVEN renderer.

The desktop layer owns process/window concerns only.  HAVEN's HTML/CSS/JS
surface remains the renderer and the Python web server remains the local
runtime and authority boundary.
"""

from .shell import (
    DesktopShell,
    DesktopShellAlreadyRunning,
    DesktopShellError,
    find_edge_executable,
)

__all__ = [
    "DesktopShell",
    "DesktopShellAlreadyRunning",
    "DesktopShellError",
    "find_edge_executable",
]
