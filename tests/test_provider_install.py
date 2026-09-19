"""Persisted activation state for an installed community provider package."""

from __future__ import annotations

import tempfile
from pathlib import Path

from haven.web.provider_install import (
    find_installed_provider,
    load_installed_provider_config,
    load_installed_providers,
    remove_installed_provider,
    save_installed_provider,
    set_installed_provider_enabled,
)
from haven.web.setup_config import SetupConfigStore


def test_nothing_installed_yet_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        assert load_installed_providers(store) == ()
        assert load_installed_provider_config(store, "philips_hue") == {}
        assert find_installed_provider(store, "philips_hue") is None


def test_save_and_reload_an_installed_provider_and_its_config():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        save_installed_provider(
            store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"}
        )

        installed = find_installed_provider(store, "philips_hue")
        assert installed is not None
        assert installed.entry_point_name == "philips_hue"
        assert installed.enabled is True
        assert load_installed_provider_config(store, "philips_hue") == {"bridge_ip": "10.0.0.5"}

        # A fresh store reading the same directory sees the same state.
        reloaded_store = SetupConfigStore(store.path)
        assert find_installed_provider(reloaded_store, "philips_hue") is not None
        assert load_installed_provider_config(reloaded_store, "philips_hue") == {"bridge_ip": "10.0.0.5"}


def test_reactivating_replaces_the_prior_config():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        save_installed_provider(store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"})
        save_installed_provider(store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.9"})
        assert load_installed_provider_config(store, "philips_hue") == {"bridge_ip": "10.0.0.9"}
        assert len(load_installed_providers(store)) == 1


def test_disabling_a_provider_keeps_its_config_but_flips_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        save_installed_provider(store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"})
        set_installed_provider_enabled(store, "philips_hue", False)
        installed = find_installed_provider(store, "philips_hue")
        assert installed.enabled is False
        assert load_installed_provider_config(store, "philips_hue") == {"bridge_ip": "10.0.0.5"}


def test_removing_a_provider_clears_both_the_index_and_its_config():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        save_installed_provider(store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"})
        remove_installed_provider(store, "philips_hue")
        assert find_installed_provider(store, "philips_hue") is None
        assert load_installed_provider_config(store, "philips_hue") == {}


def test_removing_an_uninstalled_provider_is_a_no_op():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        remove_installed_provider(store, "philips_hue")  # must not raise
        assert load_installed_providers(store) == ()


def test_two_providers_are_tracked_independently():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        save_installed_provider(store, provider_id="philips_hue", entry_point_name="philips_hue", config={"a": "1"})
        save_installed_provider(store, provider_id="matter_bridge", entry_point_name="matter", config={"b": "2"})
        ids = {p.provider_id for p in load_installed_providers(store)}
        assert ids == {"philips_hue", "matter_bridge"}
        assert load_installed_provider_config(store, "philips_hue") == {"a": "1"}
        assert load_installed_provider_config(store, "matter_bridge") == {"b": "2"}


def test_corrupt_index_file_degrades_to_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        (Path(tmp) / "installed_providers.json").write_text("not json", encoding="utf-8")
        assert load_installed_providers(store) == ()


def test_secret_fields_are_written_to_a_separate_file_from_plain_config():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        save_installed_provider(
            store,
            provider_id="philips_hue",
            entry_point_name="philips_hue",
            config={"bridge_ip": "10.0.0.5", "api_key": "super-secret"},
            secret_fields=frozenset({"api_key"}),
        )

        # The merged view a provider's build() actually sees is unaffected.
        assert load_installed_provider_config(store, "philips_hue") == {
            "bridge_ip": "10.0.0.5",
            "api_key": "super-secret",
        }

        # But on disk, the secret never lands in the plain config file.
        config_text = (data_dir / "provider_philips_hue_config.json").read_text(encoding="utf-8")
        assert "super-secret" not in config_text
        assert "10.0.0.5" in config_text

        secrets_text = (data_dir / "provider_philips_hue_secrets.json").read_text(encoding="utf-8")
        assert "super-secret" in secrets_text
        assert "10.0.0.5" not in secrets_text


def test_a_provider_with_no_secret_fields_gets_no_secrets_file():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        save_installed_provider(
            store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"}
        )
        assert not (data_dir / "provider_philips_hue_secrets.json").exists()


def test_reactivating_without_a_previously_secret_field_clears_the_stale_secrets_file():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        save_installed_provider(
            store,
            provider_id="philips_hue",
            entry_point_name="philips_hue",
            config={"api_key": "secret-one"},
            secret_fields=frozenset({"api_key"}),
        )
        assert (data_dir / "provider_philips_hue_secrets.json").exists()

        # Re-activated with a config that no longer includes the secret field.
        save_installed_provider(
            store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"}
        )
        assert not (data_dir / "provider_philips_hue_secrets.json").exists()
        assert load_installed_provider_config(store, "philips_hue") == {"bridge_ip": "10.0.0.5"}


def test_removing_a_provider_clears_its_secrets_file_too():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        save_installed_provider(
            store,
            provider_id="philips_hue",
            entry_point_name="philips_hue",
            config={"api_key": "secret-one"},
            secret_fields=frozenset({"api_key"}),
        )
        remove_installed_provider(store, "philips_hue")
        assert not (data_dir / "provider_philips_hue_secrets.json").exists()
        assert load_installed_provider_config(store, "philips_hue") == {}
