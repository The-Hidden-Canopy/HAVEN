"""Reference ONNX backend (lazy): onnxruntime inference sessions.

Backends are plugins; this is a reference implementation, not a special
case. The module top imports NOTHING beyond the stdlib -- `onnxruntime`
(and numpy, which is onnxruntime's own dependency, not HAVEN's) are
imported inside `load()`, only after the runtime is confirmed present,
so this module is always importable. When the runtime is absent,
`load()` raises the manager's `BackendMissingError` instead of an
ImportError, which the manager would otherwise report as a plain
LOAD_FAILED.

The runtime-dependent section is deliberately thin: resolve the model
path named by `descriptor.files["model"]`, open an InferenceSession,
wrap it in a handle whose `run()` maps plain lists to numpy and back and
returns the canonical `InferenceResult` (output names -> plain lists).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor
from ..results import InferenceResult


def _model_path(descriptor: ModelDescriptor, model_dir: Path | None) -> Path:
    rel = descriptor.files.get("model")
    if not rel:
        raise ValueError(
            f"onnx backend requires descriptor.files['model'] to name an .onnx file for {descriptor.id!r}"
        )
    path = Path(rel)
    if not path.is_absolute():
        path = (model_dir or Path(".")) / path
    return path


class OnnxLoadedModel:
    """Holds the InferenceSession; `unload()` closes it."""

    def __init__(self, descriptor: ModelDescriptor, session: Any, numpy: Any) -> None:
        self._descriptor = descriptor
        self._session = session
        self._numpy = numpy

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def run(self, inputs: dict[str, Any]) -> InferenceResult:
        """Run the session on numpy-free plain lists; returns plain lists."""

        feeds = {name: self._numpy.asarray(value) for name, value in inputs.items()}
        outputs = self._session.run(None, feeds)
        names = [output.name for output in self._session.get_outputs()]
        result = {name: output.tolist() for name, output in zip(names, outputs)}
        return InferenceResult(outputs=result, model_id=self._descriptor.id)

    def unload(self) -> None:
        session, self._session = self._session, None
        self._numpy = None
        if session is not None:
            del session


class OnnxModelBackend:
    """Loads an .onnx model via onnxruntime, only if it is installed."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> OnnxLoadedModel:
        try:
            import onnxruntime
        except ImportError as exc:
            from ..manager import BackendMissingError

            raise BackendMissingError(
                f"the 'onnx' backend is registered but the onnxruntime package "
                f"is not importable; install it to load {descriptor.id!r}"
            ) from exc
        import numpy as np  # onnxruntime's own dependency; gated on the runtime above

        return _load_onnx(descriptor, model_dir, onnxruntime, np)


def _load_onnx(
    descriptor: ModelDescriptor, model_dir: Path | None, onnxruntime: Any, np: Any
) -> OnnxLoadedModel:
    """Thin runtime-bound section; untestable without onnxruntime installed."""

    session = onnxruntime.InferenceSession(str(_model_path(descriptor, model_dir)))
    return OnnxLoadedModel(descriptor, session, np)


__all__ = ["OnnxLoadedModel", "OnnxModelBackend"]
