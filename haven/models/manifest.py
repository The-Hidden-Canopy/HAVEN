"""The universal `haven-model.json` manifest: one schema for every family.

Every model family -- intelligence, speech, vision, embeddings, prediction,
specialized -- describes itself with the same file, so one loader path, one
discovery pass, and one integrity check cover all of them. Declared hashes
are first-class: once a manifest declares any sha256, every declared file
must have one, because a partially verifiable model is not a candidate. A
manifest with no sha256 at all (only ever a locally synthesized one) stays
legal but unverifiable: it installs with `verified=False` and hash
verification reported as unavailable. Declared file paths are relative
basenames only -- storage installs flat, so nested or absolute paths are
rejected with the offending value. The family taxonomy (`kind`) is closed;
capabilities underneath it are deliberately open-ended.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    InvalidModelIdError,
    ModelDescriptor,
    ModelKind,
    ModelSource,
    safe_model_id,
)

SCHEMA_VERSION = "haven-model-1"

_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class ManifestError(ValueError):
    """Raised when a manifest is malformed, unsupported, or unverifiable."""


def manifest_filename() -> str:
    return "haven-model.json"


def _require_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string")
    return value.strip()


def _string_set(value: Any, *, name: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ManifestError(f"{name} must be a list of strings")
    result = frozenset()
    for item in value:
        result = result | {_require_text(item, name=name)}
    return frozenset(result)


def _relative_basename(value: Any, *, context: str) -> str:
    """A declared file must be a flat relative basename, never a path.

    Storage installs flat by basename, so anything with separators, dot-dot
    segments, a drive letter, or an absolute form is rejected with the
    offending value rather than silently re-interpreted.
    """

    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{context} must be a non-empty basename, got {value!r}")
    candidate = value.strip()
    if (
        os.path.isabs(candidate)
        or "/" in candidate
        or "\\" in candidate
        or (os.altsep is not None and os.altsep in candidate)
        or ".." in candidate
        or _DRIVE_PATTERN.match(candidate)
        or Path(candidate).name != candidate
    ):
        raise ManifestError(f"{context} must be a relative basename, got {value!r}")
    return candidate


@dataclass(frozen=True)
class ModelManifest:
    """Parsed `haven-model.json`; `to_descriptor` maps it onto the contract."""

    id: str
    version: str
    kind: ModelKind
    capabilities: frozenset[str]
    architecture: str
    backend: str
    files: dict[str, str]
    sha256: dict[str, str] = field(default_factory=dict)
    languages: frozenset[str] = frozenset()
    device_support: frozenset[str] = frozenset()
    license: str | None = None
    source: str | None = None
    endpoint: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "id", safe_model_id(self.id))
        except InvalidModelIdError as exc:
            raise ManifestError(str(exc)) from exc
        object.__setattr__(self, "version", _require_text(self.version, name="version"))
        object.__setattr__(self, "architecture", _require_text(self.architecture, name="architecture"))
        object.__setattr__(self, "backend", _require_text(self.backend, name="backend"))
        if self.endpoint is not None:
            endpoint = _require_text(self.endpoint, name="endpoint")
            if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
                raise ManifestError(f"endpoint must be an http(s) URL, got {self.endpoint!r}")
            object.__setattr__(self, "endpoint", endpoint)
        if not isinstance(self.kind, ModelKind):
            raise ManifestError(f"unknown model kind: {self.kind!r}")
        capabilities = frozenset(self.capabilities)
        if not capabilities:
            raise ManifestError("capabilities must be a non-empty list")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "languages", frozenset(self.languages))
        object.__setattr__(self, "device_support", frozenset(self.device_support))
        files = dict(self.files)
        if not files and self.endpoint is None:
            raise ManifestError("files must declare at least one role -> relative path")
        for role, rel in files.items():
            _require_text(role, name="file role")
            files[role] = _relative_basename(rel, context=f"file path for role {role!r}")
        object.__setattr__(self, "files", files)
        sha256 = dict(self.sha256)
        for rel, digest in sha256.items():
            _relative_basename(rel, context="sha256 entry")
            _require_text(digest, name=f"sha256 digest for {rel!r}")
        object.__setattr__(self, "sha256", sha256)
        uncovered = [rel for rel in files.values() if rel not in sha256]
        if sha256 and uncovered:
            raise ManifestError(
                "declared files missing sha256: " + ", ".join(sorted(uncovered))
            )
        if self.license is not None and not isinstance(self.license, str):
            raise ManifestError("license must be a string or null")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelManifest:
        if not isinstance(data, dict):
            raise ManifestError("manifest must be a JSON object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION!r}"
            )
        raw_kind = data.get("kind")
        try:
            kind = ModelKind(raw_kind)
        except ValueError:
            raise ManifestError(f"unknown model kind: {raw_kind!r}") from None
        hardware = data.get("hardware") or {}
        if not isinstance(hardware, dict):
            raise ManifestError("hardware must be an object mapping device -> boolean")
        device_support = frozenset(name for name, ok in hardware.items() if ok)
        raw_files = data.get("files")
        if not isinstance(raw_files, dict):
            raise ManifestError("files must be an object mapping role -> relative path")
        raw_sha = data.get("sha256") or {}
        if not isinstance(raw_sha, dict):
            raise ManifestError("sha256 must be an object mapping file path -> digest")
        return cls(
            id=data.get("id"),
            version=data.get("version"),
            kind=kind,
            capabilities=_string_set(data.get("capabilities"), name="capability"),
            architecture=data.get("architecture"),
            backend=data.get("backend"),
            files={str(k): str(v) for k, v in raw_files.items()},
            sha256={str(k): str(v) for k, v in raw_sha.items()},
            languages=_string_set(data.get("languages"), name="language"),
            device_support=device_support,
            license=data.get("license"),
            source=data.get("source"),
            endpoint=data.get("endpoint"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "version": self.version,
            "kind": self.kind.value,
            "capabilities": sorted(self.capabilities),
            "architecture": self.architecture,
            "backend": self.backend,
            "files": dict(self.files),
            "sha256": dict(self.sha256),
            "languages": sorted(self.languages),
            "hardware": {name: True for name in sorted(self.device_support)},
            "license": self.license,
            "source": self.source,
            "endpoint": self.endpoint,
        }

    @classmethod
    def load(cls, path: str | Path) -> ModelManifest:
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ManifestError(f"manifest not found: {path}") from None
        except json.JSONDecodeError as exc:
            raise ManifestError(f"manifest at {path} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def to_descriptor(
        self,
        source: ModelSource,
        path: str | None,
        verified: bool = False,
    ) -> ModelDescriptor:
        """Map onto the normalized contract for a given entry path."""

        return ModelDescriptor(
            id=self.id,
            kind=self.kind,
            capabilities=self.capabilities,
            backend=self.backend,
            architecture=self.architecture,
            version=self.version,
            source=source,
            path=path,
            languages=self.languages,
            device_support=self.device_support,
            license=self.license,
            verified=verified,
            files=dict(self.files),
            sha256=dict(self.sha256),
            endpoint_url=self.endpoint,
        )


__all__ = ["SCHEMA_VERSION", "ManifestError", "ModelManifest", "manifest_filename"]
