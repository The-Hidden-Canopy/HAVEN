"""Reference HTTP backend: a stdlib-only remote model endpoint client.

Backends are plugins; this is a reference implementation, not a special
case -- a community backend (mlx, rocm, coreml) registers the same way.
It loads ENDPOINT descriptors (or any descriptor carrying an http(s)
`path`) and speaks a minimal JSON envelope to the endpoint:

    POST {endpoint}/chat      {"model_id", "messages", **params}
    POST {endpoint}/complete  {"model_id", "prompt", **params}
    POST {endpoint}/{method}  {"model_id", **payload}   (capability-gated)

Responses are the parsed JSON returned by the server -- a pass-through
envelope; HAVEN does not reinterpret a remote model's wire format.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor
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

    def _post(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
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
            parsed = json.loads(body)
        except ValueError as exc:
            raise BackendConnectionError(
                f"http backend got a non-JSON response from {url}"
            ) from exc
        if not isinstance(parsed, dict):
            raise BackendConnectionError(
                f"http backend expected a JSON object from {url}, got {type(parsed).__name__}"
            )
        return parsed

    def chat(self, messages: list[dict], **params: Any) -> dict[str, Any]:
        return self._post(
            "chat", {"model_id": self._descriptor.id, "messages": messages, **params}
        )

    def complete(self, prompt: str, **params: Any) -> dict[str, Any]:
        return self._post(
            "complete", {"model_id": self._descriptor.id, "prompt": prompt, **params}
        )

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
        return self._post(name, {"model_id": self._descriptor.id, **payload})

    def transcribe(self, audio: Any, **params: Any) -> dict[str, Any]:
        return self.capability_method("transcribe", "asr", {"audio": audio, **params})

    def embed(self, text: str, **params: Any) -> dict[str, Any]:
        return self.capability_method("embed", "embedding_text", {"text": text, **params})

    def unload(self) -> None:
        """Nothing persistent to close (stateless HTTP); clears the handle."""

        self._active = False


class HttpModelBackend:
    """Loads ENDPOINT descriptors into `HttpLoadedModel` handles."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> HttpLoadedModel:
        return HttpLoadedModel(descriptor, _endpoint_url(descriptor), _http_timeout())


__all__ = ["HttpLoadedModel", "HttpModelBackend"]
