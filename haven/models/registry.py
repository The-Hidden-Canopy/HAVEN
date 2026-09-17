"""Persistent per-model records; the registry alone answers resolve().

One JSON record per model carries the full `ModelDescriptor`, the entry
source, the lifecycle state, and the loaded backend, so a fresh process can
list and resolve everything without touching storage or the network.
Persistence is append-only in spirit: a corrupt registry file raises
`RegistryError` rather than being silently truncated and rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from haven.core.time import require_aware_utc

from .contracts import ModelDescriptor, ModelKind, ModelSource, descriptor_from_dict, descriptor_to_dict
from .states import ModelState


class RegistryError(RuntimeError):
    """Raised when the registry file cannot be read as a valid registry."""


_UNSET = object()


@dataclass(frozen=True)
class ModelRecord:
    id: str
    kind: ModelKind
    state: ModelState
    source_type: ModelSource
    source_detail: str
    registered_at: datetime
    descriptor: ModelDescriptor
    loaded_backend: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "registered_at", require_aware_utc(self.registered_at, name="registered_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "state": self.state.value,
            "source": {"type": self.source_type.value, "detail": self.source_detail},
            "registered_at": self.registered_at.isoformat(),
            "descriptor": descriptor_to_dict(self.descriptor),
            "loaded_backend": self.loaded_backend,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelRecord:
        source = data.get("source") or {}
        return cls(
            id=data["id"],
            kind=ModelKind(data["kind"]),
            state=ModelState(data["state"]),
            source_type=ModelSource(source["type"]),
            source_detail=source.get("detail", ""),
            registered_at=datetime.fromisoformat(data["registered_at"]),
            descriptor=descriptor_from_dict(data["descriptor"]),
            loaded_backend=data.get("loaded_backend"),
        )


class ModelRegistry:
    def __init__(self, registry_path: str | Path) -> None:
        self.path = Path(registry_path)
        self._records: dict[str, ModelRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(f"registry at {self.path} is corrupt: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise RegistryError(f"registry at {self.path} is not a valid registry file")
        for entry in data["records"]:
            record = ModelRecord.from_dict(entry)
            self._records[record.id] = record

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": [record.to_dict() for record in self._records.values()]}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def add(self, record: ModelRecord) -> ModelRecord:
        self._records[record.id] = record
        self._save()
        return record

    def update_state(
        self,
        model_id: str,
        state: ModelState,
        *,
        loaded_backend: object = _UNSET,
    ) -> ModelRecord:
        record = self.get(model_id)
        if record is None:
            raise RegistryError(f"unknown model: {model_id}")
        backend = record.loaded_backend if loaded_backend is _UNSET else loaded_backend
        updated = replace(record, state=state, loaded_backend=backend)
        self._records[model_id] = updated
        self._save()
        return updated

    def update_record(self, record: ModelRecord) -> ModelRecord:
        self._records[record.id] = record
        self._save()
        return record

    def remove(self, model_id: str) -> bool:
        if model_id not in self._records:
            return False
        del self._records[model_id]
        self._save()
        return True

    def get(self, model_id: str) -> ModelRecord | None:
        return self._records.get(model_id)

    def list(self, state: ModelState | None = None) -> tuple[ModelRecord, ...]:
        records = tuple(self._records.values())
        if state is None:
            return records
        return tuple(record for record in records if record.state is state)


__all__ = ["ModelRecord", "ModelRegistry", "RegistryError"]
