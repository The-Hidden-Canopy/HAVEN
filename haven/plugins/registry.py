"""Local, per-installation plugin enable/disable state.

This is the *only* place a plugin's relationship to this household is
recorded, and it records exactly one thing: whether the household has
opted in. There is no credential, endpoint, or scope stored here -- opting
in means "the Hub may relay this household's exported receipts to this
plugin," never anything wider, and disabling removes even that.

Persistence mirrors `haven/models/registry.py`: one JSON file, corrupt
contents raise rather than silently reset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from haven.core.time import require_aware_utc

from .contracts import safe_plugin_id


class PluginRegistryError(RuntimeError):
    """Raised when the registry file cannot be read as a valid registry."""


@dataclass(frozen=True)
class PluginEnablement:
    plugin_id: str
    enabled: bool
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_id", safe_plugin_id(self.plugin_id))
        object.__setattr__(self, "updated_at", require_aware_utc(self.updated_at, name="updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {"plugin_id": self.plugin_id, "enabled": self.enabled, "updated_at": self.updated_at.isoformat()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginEnablement:
        return cls(
            plugin_id=data["plugin_id"],
            enabled=bool(data["enabled"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


class PluginRegistry:
    def __init__(self, registry_path: str | Path, *, clock=None) -> None:
        self.path = Path(registry_path)
        self._clock = clock or (lambda: datetime.now(tz=_utc()))
        self._records: dict[str, PluginEnablement] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginRegistryError(f"plugin registry at {self.path} is corrupt: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise PluginRegistryError(f"plugin registry at {self.path} is not a valid registry file")
        for entry in data["records"]:
            record = PluginEnablement.from_dict(entry)
            self._records[record.plugin_id] = record

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": [record.to_dict() for record in self._records.values()]}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginEnablement:
        record = PluginEnablement(plugin_id=plugin_id, enabled=enabled, updated_at=self._clock())
        self._records[record.plugin_id] = record
        self._save()
        return record

    def is_enabled(self, plugin_id: str) -> bool:
        record = self._records.get(safe_plugin_id(plugin_id))
        return record is not None and record.enabled

    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(record.plugin_id for record in self._records.values() if record.enabled)

    def list(self) -> tuple[PluginEnablement, ...]:
        return tuple(self._records.values())


def _utc():
    from datetime import timezone

    return timezone.utc


__all__ = ["PluginEnablement", "PluginRegistry", "PluginRegistryError"]
