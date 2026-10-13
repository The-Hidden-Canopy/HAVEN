"""Last-known-good marketplace cache.

Only a catalog that passed the marketplace verifier is written here. A corrupt
cache is an explicit error; it is never silently replaced with an empty list.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MarketplaceCacheError(RuntimeError):
    """The local marketplace cache cannot be trusted as a cache record."""


class MarketplaceCache:
    SCHEMA_VERSION = "haven-marketplace-cache.v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, payload: dict[str, Any], *, cached_at: datetime | None = None) -> None:
        if not isinstance(payload, dict):
            raise MarketplaceCacheError("marketplace cache payload must be an object")
        timestamp = cached_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise MarketplaceCacheError("marketplace cache timestamp must be timezone-aware")
        record = {
            "schema_version": self.SCHEMA_VERSION,
            "cached_at": timestamp.astimezone(timezone.utc).isoformat(),
            "catalog": payload,
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            raise MarketplaceCacheError(f"could not write marketplace cache {self.path}: {exc}") from exc

    def load(self) -> tuple[dict[str, Any], datetime]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise MarketplaceCacheError("marketplace cache is not present") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise MarketplaceCacheError(f"marketplace cache is unreadable: {self.path}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION:
            raise MarketplaceCacheError("marketplace cache schema is not recognized")
        catalog = raw.get("catalog")
        if not isinstance(catalog, dict):
            raise MarketplaceCacheError("marketplace cache catalog is not an object")
        cached_at = raw.get("cached_at")
        if not isinstance(cached_at, str):
            raise MarketplaceCacheError("marketplace cache cached_at is invalid")
        try:
            parsed = datetime.fromisoformat(cached_at)
        except ValueError as exc:
            raise MarketplaceCacheError("marketplace cache cached_at is invalid") from exc
        if parsed.tzinfo is None:
            raise MarketplaceCacheError("marketplace cache cached_at must be timezone-aware")
        return catalog, parsed.astimezone(timezone.utc)


__all__ = ["MarketplaceCache", "MarketplaceCacheError"]
