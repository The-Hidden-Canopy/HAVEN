"""Registry persistence: records survive a reload, transitions persist,
and a corrupt file raises a typed error instead of being truncated.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.models import (
    ModelDescriptor,
    ModelKind,
    ModelRecord,
    ModelRegistry,
    ModelSource,
    ModelState,
    RegistryError,
)


def _record(model_id: str = "m1", state: ModelState = ModelState.REGISTERED) -> ModelRecord:
    return ModelRecord(
        id=model_id,
        kind=ModelKind.INTELLIGENCE,
        state=state,
        source_type=ModelSource.DOWNLOADED,
        source_detail="https://example.com/m1/haven-model.json",
        registered_at=datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc),
        descriptor=ModelDescriptor(
            id=model_id,
            kind=ModelKind.INTELLIGENCE,
            capabilities=frozenset({"chat"}),
            backend="fake",
            architecture="stub",
            version="1.0.0",
            source=ModelSource.DOWNLOADED,
            files={"weights": "w.bin"},
        ),
    )


def _registry(tmp: str, name: str = "registry.json") -> ModelRegistry:
    return ModelRegistry(Path(tmp) / name)


def test_add_get_list_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        registry = _registry(tmp)
        registry.add(_record("m1"))
        registry.add(_record("m2", ModelState.READY))
        assert registry.get("m1").state is ModelState.REGISTERED
        assert registry.get("nope") is None
        assert {r.id for r in registry.list()} == {"m1", "m2"}
        assert {r.id for r in registry.list(ModelState.READY)} == {"m2"}


def test_records_survive_reload_with_full_descriptor():
    with tempfile.TemporaryDirectory() as tmp:
        original = _record("m1")
        _registry(tmp).add(original)
        reloaded = _registry(tmp).get("m1")
        assert reloaded == original
        assert reloaded.descriptor.capabilities == frozenset({"chat"})
        assert reloaded.registered_at.tzinfo is not None


def test_update_state_persists_and_can_set_loaded_backend():
    with tempfile.TemporaryDirectory() as tmp:
        registry = _registry(tmp)
        registry.add(_record("m1"))
        registry.update_state("m1", ModelState.LOADED, loaded_backend="fake")
        reloaded = _registry(tmp).get("m1")
        assert reloaded.state is ModelState.LOADED
        assert reloaded.loaded_backend == "fake"
        registry.update_state("m1", ModelState.READY, loaded_backend=None)
        assert _registry(tmp).get("m1").loaded_backend is None


def test_update_state_unknown_id_raises():
    with tempfile.TemporaryDirectory() as tmp:
        registry = _registry(tmp)
        with pytest.raises(RegistryError, match="unknown model"):
            registry.update_state("nope", ModelState.READY)


def test_remove_persists():
    with tempfile.TemporaryDirectory() as tmp:
        registry = _registry(tmp)
        registry.add(_record("m1"))
        assert registry.remove("m1") is True
        assert registry.remove("m1") is False
        assert _registry(tmp).get("m1") is None


def test_corrupt_json_raises_and_is_not_truncated():
    with tempfile.TemporaryDirectory() as tmp:
        registry = _registry(tmp)
        registry.add(_record("m1"))
        path = Path(tmp) / "registry.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(RegistryError, match="corrupt"):
            _registry(tmp)
        assert path.read_text(encoding="utf-8") == "{ not json"


def test_structurally_invalid_file_raises():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "registry.json"
        path.write_text(json.dumps({"records": "nope"}), encoding="utf-8")
        with pytest.raises(RegistryError, match="not a valid registry"):
            _registry(tmp)


def test_missing_registry_file_starts_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert _registry(tmp).list() == ()
