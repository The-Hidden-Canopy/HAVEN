"""The canonical model-runtime result contract.

Every backend normalizes to these BEFORE HAVEN sees output: a chat-style
call returns a `ChatResult`, and every non-chat call (embeddings, vision,
generic capability methods) returns an `InferenceResult` carrying the
backend's outputs under `outputs`. HAVEN never parses a vendor envelope --
OpenAI-ish completion maps, llama.cpp chat maps, bare strings are all
shape-mapped inside the backend that produced them, so downstream code
reads `result.text` (or `result.outputs`) without knowing which runtime
answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _normalized_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty after whitespace normalization")
    return text


def _model_id(value: Any) -> str:
    return _normalized_text(value, name="model_id")


def _optional_dict(value: Any, *, name: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a dict or None, got {type(value).__name__}")
    return dict(value)


@dataclass(frozen=True)
class ChatResult:
    """The one chat reply shape HAVEN consumes.

    `text` is the normalized answer (non-empty, whitespace-stripped);
    `model_id` records which descriptor produced it; `usage` and `raw` are
    optional vendor extras, carried verbatim for audit, never required.
    """

    text: str
    model_id: str
    usage: dict | None = None
    raw: dict | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _normalized_text(self.text, name="text"))
        object.__setattr__(self, "model_id", _model_id(self.model_id))
        object.__setattr__(self, "usage", _optional_dict(self.usage, name="usage"))
        object.__setattr__(self, "raw", _optional_dict(self.raw, name="raw"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model_id": self.model_id,
            "usage": self.usage,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class InferenceResult:
    """The non-chat result shape: structured outputs, one model_id.

    `outputs` is the backend's plain-data result mapping (embedding lists,
    detection boxes, transcription payloads); like `ChatResult.raw` the
    `raw` vendor envelope rides along for audit when there is one.
    """

    outputs: dict
    model_id: str
    raw: dict | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outputs, dict):
            raise ValueError(f"outputs must be a dict, got {type(self.outputs).__name__}")
        object.__setattr__(self, "outputs", dict(self.outputs))
        object.__setattr__(self, "model_id", _model_id(self.model_id))
        object.__setattr__(self, "raw", _optional_dict(self.raw, name="raw"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputs": dict(self.outputs),
            "model_id": self.model_id,
            "raw": self.raw,
        }


__all__ = ["ChatResult", "InferenceResult"]
