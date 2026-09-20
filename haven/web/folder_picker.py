"""Small native folder-picker seam shared by local HAVEN hosts.

The picker only asks the operating system for a folder and returns the
selection. SetupService remains responsible for validating and persisting the
permission; this module never grants filesystem access by itself.
"""

from __future__ import annotations

import platform
from pathlib import Path


def choose_folder(*, initial_dir: str | Path | None = None) -> str | None:
    """Open the Windows folder picker and return the selected path.

    ``None`` means the user cancelled. A non-Windows host reports that the
    native picker is unavailable instead of silently substituting a different
    permission flow.
    """

    if platform.system() != "Windows":
        raise RuntimeError("the native folder picker is only available on Windows")

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root,
            title="Allow HAVEN to access a folder",
            initialdir=str(initial_dir) if initial_dir else str(Path.home()),
            mustexist=True,
        )
        return selected or None
    finally:
        root.destroy()


__all__ = ["choose_folder"]
