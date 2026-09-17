"""Backend plugin protocol; the registry itself is a plain name -> loader map.

Backends (http, onnx, transformers, llama_cpp, community mlx/rocm/coreml/
...) are plugins: each implements `ModelBackend` and registers under a
name. HAVEN ships reference implementations in this package (see
`reference.reference_backends`): http is stdlib and always active; the
ML loaders are lazy and only activate when their runtime is importable.
A bare `BackendRegistry` still starts empty, and `resolve` returns None
(never raises) so the manager can convert a miss into the explicit state
BACKEND_MISSING -- never a silent fallback. Tests register a fake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..contracts import ModelDescriptor


class LoadedModel(Protocol):
    """An opaque handle to a loaded model; the manager only unloads it."""

    @property
    def descriptor(self) -> ModelDescriptor: ...

    def unload(self) -> None: ...


class ModelBackend(Protocol):
    """Loads a descriptor into a running model; plugins implement this."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> LoadedModel: ...


class BackendRegistry:
    """Name -> loader map; `resolve` returns None (never raises) so the
    manager can convert a miss into the explicit BACKEND_MISSING state."""

    def __init__(self) -> None:
        self._loaders: dict[str, ModelBackend] = {}

    def register(self, backend_name: str, loader: ModelBackend) -> None:
        if not backend_name or not backend_name.strip():
            raise ValueError("backend_name must be a non-empty string")
        self._loaders[backend_name.strip()] = loader

    def resolve(self, backend: str) -> ModelBackend | None:
        return self._loaders.get(backend)

    def registered(self) -> tuple[str, ...]:
        return tuple(self._loaders)


__all__ = ["BackendRegistry", "LoadedModel", "ModelBackend"]
