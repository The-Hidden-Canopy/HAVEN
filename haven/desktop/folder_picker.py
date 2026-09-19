"""Small native folder-picker seam used by HAVEN Desktop.

The picker is deliberately outside the setup service.  Setup still owns
permission persistence; this module only asks the operating system for a
folder and returns the user's selection.
"""

from __future__ import annotations

import platform
from pathlib import Path


def choose_folder(*, initial_dir: str | Path | None = None) -> str | None:
    """Open the Windows folder picker and return the selected path.

    ``None`` means the user cancelled.  A non-Windows host reports an
    unavailable native capability instead of silently using a different UI.
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
