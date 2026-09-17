"""The normalized internal contract every model speaks, whatever it is.

Models are interchangeable local capabilities, not the product. HAVEN's
runtime resolves by `kind` + required `capabilities` + optional `languages`
-- NEVER by model name, architecture, or backend. Those three are descriptor
metadata for loaders, not routing keys: a qwen checkpoint on transformers
and a gguf build of something else are the same slot if they cover the same
capabilities. Every model family -- intelligence, speech, vision,
embeddings, prediction, specialized -- enters through the same three paths
(Download, Load Local, Register External) and is represented here as one
`ModelDescriptor`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelKind(str, Enum):
    """The closed family taxonomy.

    Capabilities are the open vocabulary beneath this (chat, wake_word, vad,
    asr, object_detection, embedding_text, ...); the families are not.
    """

    INTELLIGENCE = "intelligence"
    SPEECH = "speech"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    PREDICTION = "prediction"
    SPECIALIZED = "specialized"


class ModelSource(str, Enum):
    """How a model entered the system: one of exactly three paths."""

    DOWNLOADED = "downloaded"
    LOCAL = "local"
    ENDPOINT = "endpoint"


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class InvalidModelIdError(ValueError):
    """Raised when a model id is not a safe storage-safe identifier."""


_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def safe_model_id(value: str) -> str:
    """Validate a model id: a flat basename slug, never a path.

    Externally supplied ids (manifests, folder names, repo names) end up in
    filesystem paths, so anything with separators, dot-dot segments, drive
    letters, or characters outside the slug alphabet is rejected here before
    it can reach storage.
    """

    if not isinstance(value, str):
        raise InvalidModelIdError(f"model id must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if not candidate:
        raise InvalidModelIdError("model id must be a non-empty string")
    if ".." in candidate or "/" in candidate or "\\" in candidate:
        raise InvalidModelIdError(f"model id must not contain path separators or '..': {candidate!r}")
    if not _ID_PATTERN.fullmatch(candidate):
        raise InvalidModelIdError(
            f"model id must match ^[a-z0-9][a-z0-9._-]{{0,63}}$: {candidate!r}"
        )
    return candidate


@dataclass(frozen=True)
class ModelDescriptor:
    """The internal normalized object; the manager works only in these."""

    id: str
    kind: ModelKind
    capabilities: frozenset[str]
    backend: str
    architecture: str
    version: str
    source: ModelSource
    path: str | None = None
    endpoint_url: str | None = None
    languages: frozenset[str] = frozenset()
    device_support: frozenset[str] = frozenset()
    license: str | None = None
    verified: bool = False
    files: dict[str, str] = field(default_factory=dict)
    sha256: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", safe_model_id(self.id))
        object.__setattr__(self, "version", _require_text(self.version, name="version"))
        object.__setattr__(self, "backend", _require_text(self.backend, name="backend"))
        object.__setattr__(self, "architecture", _require_text(self.architecture, name="architecture"))
        if not isinstance(self.kind, ModelKind):
            object.__setattr__(self, "kind", ModelKind(self.kind))
        if not isinstance(self.source, ModelSource):
            object.__setattr__(self, "source", ModelSource(self.source))
        if self.endpoint_url is not None:
            endpoint = _require_text(self.endpoint_url, name="endpoint_url")
            if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
                raise ValueError("endpoint_url must be an http(s) URL")
            object.__setattr__(self, "endpoint_url", endpoint)
        capabilities = frozenset(_require_text(c, name="capability") for c in self.capabilities)
        if not capabilities:
            raise ValueError("capabilities must be a non-empty set")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "languages", frozenset(self.languages))
        object.__setattr__(self, "device_support", frozenset(self.device_support))
        object.__setattr__(self, "files", dict(self.files))
        object.__setattr__(self, "sha256", dict(self.sha256))
        if self.source is ModelSource.ENDPOINT:
            if self.endpoint_url is None:
                raise ValueError("endpoint source requires an endpoint_url")
            if self.files:
                raise ValueError("endpoint descriptors carry no files")
        else:
            # File-based sources may ALSO declare an endpoint_url: local
            # artifacts (tokens, configs) plus remote inference is a
            # legitimate topology (manifests carry it via `endpoint`).
            if not self.files:
                raise ValueError("file-based sources (downloaded/local) require a non-empty files map")

    def supports_all(self, capabilities: set[str] | frozenset[str]) -> bool:
        return set(capabilities).issubset(self.capabilities)


def descriptor_to_dict(descriptor: ModelDescriptor) -> dict[str, Any]:
    """Serialize to a JSON-safe dict; the registry persists exactly this."""

    return {
        "id": descriptor.id,
        "kind": descriptor.kind.value,
        "capabilities": sorted(descriptor.capabilities),
        "backend": descriptor.backend,
        "architecture": descriptor.architecture,
        "version": descriptor.version,
        "source": descriptor.source.value,
        "path": descriptor.path,
        "endpoint": descriptor.endpoint_url,
        "languages": sorted(descriptor.languages),
        "device_support": sorted(descriptor.device_support),
        "license": descriptor.license,
        "verified": descriptor.verified,
        "files": dict(descriptor.files),
        "sha256": dict(descriptor.sha256),
    }


def descriptor_from_dict(data: dict[str, Any]) -> ModelDescriptor:
    """Rebuild a descriptor from `descriptor_to_dict` output."""

    return ModelDescriptor(
        id=data["id"],
        kind=ModelKind(data["kind"]),
        capabilities=frozenset(data.get("capabilities", ())),
        backend=data["backend"],
        architecture=data["architecture"],
        version=data["version"],
        source=ModelSource(data["source"]),
        path=data.get("path"),
        endpoint_url=data.get("endpoint"),
        languages=frozenset(data.get("languages", ())),
        device_support=frozenset(data.get("device_support", ())),
        license=data.get("license"),
        verified=bool(data.get("verified", False)),
        files=dict(data.get("files", {})),
        sha256=dict(data.get("sha256", {})),
    )


def descriptor_to_json(descriptor: ModelDescriptor) -> str:
    return json.dumps(descriptor_to_dict(descriptor), sort_keys=True)


__all__ = [
    "InvalidModelIdError",
    "ModelDescriptor",
    "ModelKind",
    "ModelSource",
    "descriptor_from_dict",
    "descriptor_to_dict",
    "descriptor_to_json",
    "safe_model_id",
]
