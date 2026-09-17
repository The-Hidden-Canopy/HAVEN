"""Reference llama.cpp backend (lazy): single-file .gguf models.

Backends are plugins; this is a reference implementation, not a special
case. The module top imports NOTHING beyond the stdlib -- `llama_cpp` is
imported inside `load()`, so this module is always importable. When the
runtime is absent, `load()` raises the manager's `BackendMissingError`
instead of an ImportError, which the manager would otherwise report as a
plain LOAD_FAILED.

The runtime-dependent section is deliberately thin: resolve the .gguf
path named by `descriptor.files["model"]`, construct `llama_cpp.Llama`,
wrap it in a handle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor

_CONTEXT_SIZE = 4096


def _model_path(descriptor: ModelDescriptor, model_dir: Path | None) -> Path:
    rel = descriptor.files.get("model")
    if not rel:
        raise ValueError(
            f"llama_cpp backend requires descriptor.files['model'] to name a .gguf file for {descriptor.id!r}"
        )
    path = Path(rel)
    if not path.is_absolute():
        path = (model_dir or Path(".")) / path
    return path


class LlamaCppLoadedModel:
    """Holds the llama_cpp.Llama instance; `unload()` drops the reference."""

    def __init__(self, descriptor: ModelDescriptor, model: Any) -> None:
        self._descriptor = descriptor
        self._model = model

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def chat(self, messages: list[dict], **params: Any) -> dict[str, Any]:
        return self._model.create_chat_completion(messages=messages, **params)

    def unload(self) -> None:
        self._model = None


class LlamaCppModelBackend:
    """Loads a .gguf checkpoint via llama_cpp, only if it is installed."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> LlamaCppLoadedModel:
        try:
            import llama_cpp
        except ImportError as exc:
            from ..manager import BackendMissingError

            raise BackendMissingError(
                f"the 'llama_cpp' backend is registered but the llama_cpp package "
                f"is not importable; install it to load {descriptor.id!r}"
            ) from exc
        return _load_llama_cpp(descriptor, model_dir, llama_cpp)


def _load_llama_cpp(
    descriptor: ModelDescriptor, model_dir: Path | None, llama_cpp: Any
) -> LlamaCppLoadedModel:
    """Thin runtime-bound section; untestable without llama_cpp installed."""

    model = llama_cpp.Llama(model=str(_model_path(descriptor, model_dir)), n_ctx=_CONTEXT_SIZE)
    return LlamaCppLoadedModel(descriptor, model)


__all__ = ["LlamaCppLoadedModel", "LlamaCppModelBackend"]
