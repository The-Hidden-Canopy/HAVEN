"""Backend plugin protocol plus the reference backend set.

Backends are plugins: a community backend (mlx, rocm, coreml) registers
exactly the way the reference implementations do -- a `ModelBackend`
under a name in a `BackendRegistry`. `reference_backends()` is the set
HAVEN wires by default so a stock install can genuinely load models:
"http" (stdlib urllib, always active) plus lazy loaders for
"transformers", "llama_cpp", and "onnx" that activate only when their
runtime is importable and otherwise raise the manager's
`BackendMissingError`. A bare `BackendRegistry()` still ships empty; a
load whose manifest names a backend with no registered loader fails with
the explicit state BACKEND_MISSING -- never a silent fallback.
"""

from .protocol import BackendRegistry, LoadedModel, ModelBackend
from .reference import BackendConnectionError, reference_backends

__all__ = [
    "BackendConnectionError",
    "BackendRegistry",
    "LoadedModel",
    "ModelBackend",
    "reference_backends",
]
