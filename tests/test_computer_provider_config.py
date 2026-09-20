"""Persisted config for the built-in computer/filesystem provider."""

from __future__ import annotations

import tempfile
from pathlib import Path

from haven.web.computer_provider import (
    ComputerProviderConfig,
    build_filesystem_provider,
    load_computer_provider_config,
    save_computer_provider_config,
)
from haven.web.setup_config import SetupConfigStore


def test_default_is_disabled_with_no_roots():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        config = load_computer_provider_config(store)
    assert config == ComputerProviderConfig()
    assert config.enabled is False
    assert config.allowed_roots == ()
    assert config.read_only is True


def test_legacy_config_without_read_only_field_migrates_to_safe_default():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        (Path(tmp) / "computer_provider.json").write_text(
            '{"enabled": true, "allowed_roots": ["C:/Docs"]}', encoding="utf-8"
        )
        config = load_computer_provider_config(store)

    assert config.enabled is True
    assert config.allowed_roots == ("C:/Docs",)
    assert config.read_only is True


def test_save_and_reload_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        save_computer_provider_config(
            store, ComputerProviderConfig(enabled=True, allowed_roots=("C:/Docs", "C:/Downloads"), read_only=True)
        )
        reloaded = load_computer_provider_config(SetupConfigStore(store.path))
    assert reloaded == ComputerProviderConfig(enabled=True, allowed_roots=("C:/Docs", "C:/Downloads"), read_only=True)


def test_corrupt_file_degrades_to_default():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        (Path(tmp) / "computer_provider.json").write_text("not json", encoding="utf-8")
        config = load_computer_provider_config(store)
    assert config == ComputerProviderConfig()


def test_build_filesystem_provider_returns_none_when_disabled():
    config = ComputerProviderConfig(enabled=False, allowed_roots=("C:/Docs",))
    assert build_filesystem_provider(config, scope_id="project:haven") is None


def test_build_filesystem_provider_returns_none_with_no_roots():
    config = ComputerProviderConfig(enabled=True, allowed_roots=())
    assert build_filesystem_provider(config, scope_id="project:haven") is None


def test_build_filesystem_provider_returns_none_for_a_root_that_no_longer_exists():
    with tempfile.TemporaryDirectory() as tmp:
        missing = str(Path(tmp) / "moved-away")
        config = ComputerProviderConfig(enabled=True, allowed_roots=(missing,))
    assert build_filesystem_provider(config, scope_id="project:haven") is None


def test_build_filesystem_provider_returns_a_real_provider_when_configured():
    with tempfile.TemporaryDirectory() as tmp:
        config = ComputerProviderConfig(enabled=True, allowed_roots=(tmp,))
        provider = build_filesystem_provider(config, scope_id="project:haven")
        assert provider is not None
        records = provider.observe()
        assert records is not None
        assert all(
            record.capabilities == ("filesystem.read", "filesystem.open", "filesystem.reveal")
            for record in records
        )
