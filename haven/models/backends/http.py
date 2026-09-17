"""Reference HTTP backend: a stdlib-only remote model endpoint client.

Backends are plugins; this is a reference implementation, not a special
case -- a community backend (mlx, rocm, coreml) registers the same way.
It loads ENDPOINT descriptors (or any descriptor carrying an http(s)
`path`) and speaks a minimal JSON envelope to the endpoint:

    POST {endpoint}/chat      {"model_id", "messages", **params}
    POST {endpoint}/complete  {"model_id", "prompt", **params}
    POST {endpoint}/{method}  {"model_id", **payload}   (capability-gated)

Responses are normalized to the canonical result contract before HAVEN
sees them: `chat`/`complete` accept a plain {"text": ...} mapping, an
OpenAI-chat-ish {"choices": [{"message": {"content": ...}}]}, a
completion-ish {"choices": [{"text": ...}]}, or a bare JSON string, and
return a `ChatResult`; `transcribe`/`embed` return an `InferenceResult`
wrapping the endpoint's payload. Anything unrecognized is a
`BackendConnectionError` naming the shape -- HAVEN never parses a vendor
envelope itself.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor
from ..results import ChatResult, InferenceResult
from .reference import BackendConnectionError

_TIMEOUT_ENV = "HAVEN_HTTP_BACKEND_TIMEOUT"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_USER_AGENT = "haven-model-backend"


def _http_timeout() -> float:
    raw = os.environ.get(_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _endpoint_url(descriptor: ModelDescriptor) -> str:
    url = (descriptor.endpoint_url or descriptor.path or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(
            f"http backend requires an http(s) endpoint URL; "
            f"descriptor {descriptor.id!r} has endpoint_url {descriptor.endpoint_url!r} "
            f"and path {descriptor.path!r}"
        )
    return url.rstrip("/")


class HttpLoadedModel:
    """A stateless handle; every call is a fresh POST, nothing persists."""

    def __init__(self, descriptor: ModelDescriptor, endpoint: str, timeout: float) -> None:
        self._descriptor = descriptor
        self._endpoint = endpoint
        self._timeout = timeout
        self._active = True

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def _post(self, route: str, payload: dict[str, Any]) -> Any:
        if not self._active:
            raise RuntimeError(f"model {self._descriptor.id!r} is unloaded")
        url = f"{self._endpoint}/{route}"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendConnectionError(f"http backend could not reach {url}: {exc}") from exc
        try:
            return json.loads(body)
        except ValueError as exc:
            raise BackendConnectionError(
                f"http backend got a non-JSON response from {url}"
            ) from exc

    @staticmethod
    def _extract_text(parsed: Any, *, route: str) -> str:
        """Map a chat/complete response onto normalized text.

        Accepted shapes: {"text": str}, OpenAI-chat-ish choices with a
        message content, completion-ish choices with a text, or a bare
        JSON string. Anything else names its shape in the error.
        """

        if isinstance(parsed, str):
            text = parsed.strip()
            if text:
                return text
        elif isinstance(parsed, dict):
            text = parsed.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            choices = parsed.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                completion = choices[0].get("text")
                if isinstance(completion, str) and completion.strip():
                    return completion.strip()
        raise BackendConnectionError(
            f"http backend got an unrecognized response shape from "
            f"/{route}: {type(parsed).__name__}"
        )

    def _chat_result(self, parsed: Any, *, route: str) -> ChatResult:
        usage = parsed.get("usage") if isinstance(parsed, dict) else None
        return ChatResult(
            text=self._extract_text(parsed, route=route),
            model_id=self._descriptor.id,
            usage=usage if isinstance(usage, dict) else None,
            raw=parsed if isinstance(parsed, dict) else None,
        )

    def chat(self, messages: list[dict], **params: Any) -> ChatResult:
        parsed = self._post(
            "chat", {"model_id": self._descriptor.id, "messages": messages, **params}
        )
        return self._chat_result(parsed, route="chat")

    def complete(self, prompt: str, **params: Any) -> ChatResult:
        parsed = self._post(
            "complete", {"model_id": self._descriptor.id, "prompt": prompt, **params}
        )
        return self._chat_result(parsed, route="complete")

    def capability_method(
        self, name: str, requires: str | list[str] | tuple[str, ...], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST `{endpoint}/{name}` when the descriptor carries the capability.

        `requires` is the capability (or capabilities) the endpoint model
        must advertise; a descriptor without them raises instead of
        guessing what an incapable endpoint would do.
        """

        required = frozenset((requires,) if isinstance(requires, str) else requires)
        if not self._descriptor.supports_all(required):
            raise RuntimeError(
                f"model {self._descriptor.id!r} cannot {name!r}: requires "
                f"{sorted(required)}, descriptor has {sorted(self._descriptor.capabilities)}"
            )
        parsed = self._post(name, {"model_id": self._descriptor.id, **payload})
        if not isinstance(parsed, dict):
            raise BackendConnectionError(
                f"http backend expected a JSON object from /{name}, "
                f"got {type(parsed).__name__}"
            )
        return parsed

    def transcribe(self, audio: Any, **params: Any) -> InferenceResult:
        payload = self.capability_method("transcribe", "asr", {"audio": audio, **params})
        return InferenceResult(
            outputs={"result": payload}, model_id=self._descriptor.id, raw=payload
        )

    def embed(self, text: str, **params: Any) -> InferenceResult:
        payload = self.capability_method("embed", "embedding_text", {"text": text, **params})
        return InferenceResult(
            outputs={"result": payload}, model_id=self._descriptor.id, raw=payload
        )

    def unload(self) -> None:
        """Nothing persistent to close (stateless HTTP); clears the handle."""

        self._active = False


class HttpModelBackend:
    """Loads ENDPOINT descriptors into `HttpLoadedModel` handles."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> HttpLoadedModel:
        return HttpLoadedModel(descriptor, _endpoint_url(descriptor), _http_timeout())


__all__ = ["HttpLoadedModel", "HttpModelBackend"]
