"""The reference backend set HAVEN wires by default.

Backends are plugins: a community backend (mlx, rocm, coreml) registers
exactly the way these reference implementations do -- a `ModelBackend`
under a name in a `BackendRegistry`. What ships here is the set a stock
install can genuinely load with:

- "http"        stdlib `urllib`, fully real, always active; endpoint
                descriptors talk to a remote model server.
- "transformers" lazy: activates only when `transformers` is importable.
- "llama_cpp"    lazy: activates only when `llama_cpp` is importable.
- "onnx"         lazy: activates only when `onnxruntime` is importable.
- "piper"        lazy: activates only when the native piper executable
                (`scripts/install_piper.py`) is installed -- not a Python
                package, a real native TTS tool invoked via subprocess.

A lazy loader whose runtime is absent raises the manager's
`BackendMissingError` -- the "registered but the runtime is not here"
signal, distinct from a load that was attempted and failed. A bare
`BackendRegistry()` still ships empty; this factory is what a deployer
(or HAVEN itself) passes to the manager.
"""

from __future__ import annotations

from ..results import ChatResult, InferenceResult
from .protocol import BackendRegistry


class BackendConnectionError(RuntimeError):
    """A backend could not reach or read from its external resource."""


def reference_backends() -> BackendRegistry:
    """The default backend set: real stdlib http plus lazy ML loaders.

    Imported inside the function so importing this module never drags in
    the per-backend modules (which keeps the http backend's error class
    importable without any optional runtime present).
    """

    from .http import HttpModelBackend
    from .llama_cpp_backend import LlamaCppModelBackend
    from .onnx_backend import OnnxModelBackend
    from .piper_native import PiperNativeBackend
    from .transformers_backend import TransformersModelBackend

    registry = BackendRegistry()
    registry.register("http", HttpModelBackend())
    registry.register("transformers", TransformersModelBackend())
    registry.register("llama_cpp", LlamaCppModelBackend())
    registry.register("onnx", OnnxModelBackend())
    registry.register("piper", PiperNativeBackend())
    return registry


__all__ = [
    "BackendConnectionError",
    "ChatResult",
    "InferenceResult",
    "reference_backends",
]
