"""Backend plugin protocol; the registry here ships EMPTY on purpose.

Real onnx/transformers/gguf/remote_http loaders are deployer- or
plugin-registered (community plugins like haven-model-mlx add backends
without HAVEN Core absorbing runtimes). A load with no loader for the
manifest's backend fails with the explicit state BACKEND_MISSING.
"""

from .protocol import BackendRegistry, LoadedModel, ModelBackend

__all__ = ["BackendRegistry", "LoadedModel", "ModelBackend"]
