"""Curated catalog files: pointers to manifests, never models themselves.

HAVEN ships NO hardwired model list -- `default_catalog()` is empty, and a
deployment curates its own catalog files. Community publishers distribute
catalog files the same way; a user who already has a manifest URL can simply
paste it, so the catalog is a convenience layer over `inspect_url`, not a
gate. Entries point at manifest URLs (http(s) only): inspecting before
installing means the bytes that matter are read before anything is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ModelKind


class CatalogError(ValueError):
    """Raised when a catalog file is malformed or an entry is invalid."""


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    kind: ModelKind
    manifest_url: str
    description: str = ""
    size_hint_mb: int | None = None
    license: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise CatalogError("catalog entry id must be a non-empty string")
        if not isinstance(self.kind, ModelKind):
            raise CatalogError(f"catalog entry {self.id!r} has unknown kind: {self.kind!r}")
        if not (self.manifest_url.startswith("http://") or self.manifest_url.startswith("https://")):
            raise CatalogError(
                f"catalog entry {self.id!r} manifest_url must be http(s): {self.manifest_url!r}"
            )


def _entry_from_dict(data: dict[str, Any]) -> CatalogEntry:
    if not isinstance(data, dict):
        raise CatalogError("catalog entries must be JSON objects")
    raw_kind = data.get("kind")
    try:
        kind = ModelKind(raw_kind)
    except ValueError:
        raise CatalogError(f"catalog entry {data.get('id')!r} has unknown kind: {raw_kind!r}") from None
    size = data.get("size_hint_mb")
    if size is not None and (not isinstance(size, int) or size < 0):
        raise CatalogError(f"catalog entry {data.get('id')!r} size_hint_mb must be a non-negative integer")
    return CatalogEntry(
        id=data.get("id"),
        kind=kind,
        manifest_url=data.get("manifest_url"),
        description=data.get("description", "") or "",
        size_hint_mb=size,
        license=data.get("license"),
    )


def load_catalog(path: str | Path) -> tuple[CatalogEntry, ...]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CatalogError(f"catalog not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog at {path} is not valid JSON: {exc}") from exc
    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict) and isinstance(data.get("entries"), list):
        raw_entries = data["entries"]
    else:
        raise CatalogError(f"catalog at {path} must be a list or an object with an 'entries' list")
    return tuple(_entry_from_dict(entry) for entry in raw_entries)


def default_catalog() -> tuple[CatalogEntry, ...]:
    """HAVEN Core ships no model list; curation populates catalog files."""

    return ()


__all__ = ["CatalogEntry", "CatalogError", "default_catalog", "load_catalog"]
