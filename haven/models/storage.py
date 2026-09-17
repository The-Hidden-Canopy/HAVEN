"""The on-disk models-root layout.

Models live under `<root>/<kind>/<id>/` with their `haven-model.json` and
declared files copied flat by basename. The manager may hold several models
at once (wake + VAD + ASR + vision + agent + embeddings all loaded
simultaneously is the normal topology), so the layout is per-model and
never single-select.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .manifest import ModelManifest, manifest_filename


class StorageError(RuntimeError):
    """Raised when an install/remove cannot be performed as asked."""


def default_models_root() -> Path:
    """`~/.haven/models`; pathlib home resolves HOME fallbacks portably."""

    return Path.home() / ".haven" / "models"


class ModelStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def model_dir(self, kind, model_id: str) -> Path:
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        return self.root / kind_value / model_id

    def is_installed(self, kind, model_id: str) -> bool:
        return self.model_dir(kind, model_id).is_dir()

    def install(
        self,
        manifest: ModelManifest,
        source_dir: str | Path,
        *,
        replace: bool = False,
    ) -> Path:
        """Copy the declared files flat by basename plus the manifest."""

        dest = self.model_dir(manifest.kind, manifest.id)
        if dest.is_dir():
            if not replace:
                raise StorageError(f"model already installed: {manifest.kind.value}/{manifest.id}")
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        source_dir = Path(source_dir)
        for role, rel in manifest.files.items():
            src = source_dir / rel
            if not src.is_file():
                shutil.rmtree(dest, ignore_errors=True)
                raise StorageError(f"declared file for role {role!r} not found: {src}")
            shutil.copyfile(src, dest / Path(rel).name)
        manifest.save(dest / manifest_filename())
        return dest

    def install_endpoint(
        self,
        manifest: ModelManifest,
        *,
        replace: bool = False,
    ) -> Path:
        """Record an endpoint model: manifest only, no files."""

        dest = self.model_dir(manifest.kind, manifest.id)
        if dest.is_dir():
            if not replace:
                raise StorageError(f"model already installed: {manifest.kind.value}/{manifest.id}")
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        manifest.save(dest / manifest_filename())
        return dest

    def remove(self, kind, model_id: str) -> bool:
        """Remove an installed model; returns True if something was removed."""

        dest = self.model_dir(kind, model_id)
        if not dest.is_dir():
            return False
        shutil.rmtree(dest)
        return True


__all__ = ["ModelStorage", "StorageError", "default_models_root"]
