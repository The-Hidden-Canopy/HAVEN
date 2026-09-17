"""Reference transformers backend (lazy): Hugging Face checkpoints.

Backends are plugins; this is a reference implementation, not a special
case. The module top imports NOTHING beyond the stdlib -- `transformers`
is imported inside `load()`, so this module is always importable. When
the runtime is absent, `load()` raises the manager's `BackendMissingError`
(the "registered but the runtime is not here" signal) instead of an
ImportError, which the manager would otherwise report as a plain
LOAD_FAILED.

The runtime-dependent section is deliberately thin: resolve a source
path, build tokenizer + causal LM, wrap them in a handle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor


def _source_path(descriptor: ModelDescriptor, model_dir: Path | None) -> str:
    source = descriptor.path or (str(model_dir) if model_dir is not None else None)
    if not source:
        raise ValueError(f"transformers backend needs descriptor.path or model_dir for {descriptor.id!r}")
    return source


class TransformersLoadedModel:
    """Holds tokenizer + model references; `unload()` drops them."""

    def __init__(self, descriptor: ModelDescriptor, tokenizer: Any, model: Any) -> None:
        self._descriptor = descriptor
        self._tokenizer = tokenizer
        self._model = model

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def chat(self, messages: list[dict], **params: Any) -> str:
        """Minimal generic pipeline: apply_chat_template -> generate -> decode."""

        ids = self._tokenizer.apply_chat_template(
            messages, tokenize=True, return_tensors="pt", add_generation_prompt=True
        )
        generated = self._model.generate(ids, **params)
        return self._tokenizer.decode(
            generated[0][ids.shape[-1] :], skip_special_tokens=True
        )

    def unload(self) -> None:
        self._tokenizer = None
        self._model = None


class TransformersModelBackend:
    """Loads a local checkpoint via transformers, only if it is installed."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> TransformersLoadedModel:
        try:
            import transformers  # noqa: F401
        except ImportError as exc:
            from ..manager import BackendMissingError

            raise BackendMissingError(
                f"the 'transformers' backend is registered but the transformers "
                f"package is not importable; install it to load {descriptor.id!r}"
            ) from exc
        return _load_transformers(descriptor, model_dir)


def _load_transformers(
    descriptor: ModelDescriptor, model_dir: Path | None
) -> TransformersLoadedModel:
    """Thin runtime-bound section; untestable without torch installed."""

    from transformers import AutoModelForCausalLM, AutoTokenizer

    source = _source_path(descriptor, model_dir)
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=False, local_files_only=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        source, trust_remote_code=False, local_files_only=True
    )
    return TransformersLoadedModel(descriptor, tokenizer, model)


__all__ = ["TransformersLoadedModel", "TransformersModelBackend"]
