"""Backend plugin protocol; HAVEN Core ships ZERO registered backends.

Backends (onnx, transformers, gguf, remote_http, community mlx/rocm/coreml/
...) are deployer- or plugin-registered: a community plugin like
haven-model-mlx adds a backend without HAVEN Core absorbing the runtime.
This repo's `BackendRegistry` therefore ships empty, and a load whose
manifest names a backend with no registered loader fails with the explicit
state BACKEND_MISSING -- never a silent fallback. Tests register a fake.
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
