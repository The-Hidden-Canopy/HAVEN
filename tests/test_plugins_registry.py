"""PluginRegistry: local, file-backed enable/disable state only."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from haven.plugins import InvalidPluginIdError, PluginRegistry, PluginRegistryError

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _registry(tmp_path, **kwargs):
    return PluginRegistry(tmp_path / "plugins.json", clock=lambda: NOW, **kwargs)


def test_new_plugin_is_disabled_by_default(tmp_path):
    registry = _registry(tmp_path)
    assert registry.is_enabled("traceglass") is False


def test_enable_then_disable_round_trips(tmp_path):
    registry = _registry(tmp_path)
    registry.set_enabled("traceglass", True)
    assert registry.is_enabled("traceglass") is True
    assert registry.enabled_ids() == ("traceglass",)
    registry.set_enabled("traceglass", False)
    assert registry.is_enabled("traceglass") is False
    assert registry.enabled_ids() == ()


def test_state_persists_across_a_fresh_instance(tmp_path):
    path = tmp_path / "plugins.json"
    PluginRegistry(path, clock=lambda: NOW).set_enabled("ghost-teacher", True)
    reloaded = PluginRegistry(path, clock=lambda: NOW)
    assert reloaded.is_enabled("ghost-teacher") is True


def test_rejects_an_unsafe_plugin_id(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(InvalidPluginIdError):
        registry.set_enabled("../escape", True)


def test_corrupt_registry_file_raises_rather_than_resetting_silently(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(PluginRegistryError):
        PluginRegistry(path)


def test_registry_file_that_is_not_the_expected_shape_raises(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_text('{"records": "not-a-list"}', encoding="utf-8")
    with pytest.raises(PluginRegistryError):
        PluginRegistry(path)


def test_list_reflects_every_recorded_choice(tmp_path):
    registry = _registry(tmp_path)
    registry.set_enabled("traceglass", True)
    registry.set_enabled("ghost-teacher", False)
    ids = sorted(record.plugin_id for record in registry.list())
    assert ids == ["ghost-teacher", "traceglass"]
