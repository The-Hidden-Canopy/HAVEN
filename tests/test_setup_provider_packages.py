"""SetupService's provider-package endpoints: list/install/enable/uninstall
a community provider discovered through `haven.providers` entry points --
the setup-wizard-facing half of `haven/providers/loader.py`.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from haven.core.consequence import ConsequenceClass
from haven.providers.plugin import ProviderConfigField, ProviderManifest, ProviderOperationalProfile
from haven.web.demo import DemoDirector
from haven.web.provider_install import find_installed_provider, load_installed_provider_config, save_installed_provider
from haven.web.setup_config import SetupConfigStore
from haven.web.setup_service import SetupService

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


class _FakeHueInstance:
    def __init__(self, config):
        self.config = dict(config)


class FakeHuePluginForSetupServiceTest:
    def describe(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="philips_hue",
            kind="execution",
            capabilities=frozenset({"light.turn_on"}),
            display_name="Philips Hue",
            description="test fixture",
            permissions=("network access to your Hue bridge",),
            config_fields=(ProviderConfigField(name="bridge_ip", label="Bridge IP"),),
        )

    def build(self, *, config):
        if "bridge_ip" not in config:
            raise ValueError("bridge_ip is required")
        return _FakeHueInstance(config)


FAKE_HUE_PLUGIN = FakeHuePluginForSetupServiceTest()


class FakeCommsPluginWithOperationalProfile:
    """A fixture provider that actually declares §5.2's operational
    metadata, unlike `FakeHuePluginForSetupServiceTest` (which -- like most
    providers written before this profile existed -- relies entirely on
    the all-conservative defaults)."""

    def describe(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="fake_mail",
            kind="communications",
            capabilities=frozenset({"email.read", "email.send"}),
            display_name="Fake Mail",
            description="test fixture",
            operational=ProviderOperationalProfile(
                observation=True,
                read=True,
                mutation=True,
                required_credential_scopes=("mail.read", "mail.send"),
                destructive_action_classes=(ConsequenceClass.REVERSIBLE_EXTERNAL,),
                offline_behavior="queues outbound mail locally until reachable again",
                refresh_strategy="poll:60s",
            ),
        )

    def build(self, *, config):
        return object()


FAKE_COMMS_PLUGIN = FakeCommsPluginWithOperationalProfile()


class _BrokenPlugin:
    def describe(self):
        raise RuntimeError("boom")

    def build(self, *, config):
        raise RuntimeError("boom")


def _service(data_dir: Path) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(store=store, director=DemoDirector(clock=lambda: NOW), clock=lambda: NOW)


def _patch_entry_points(monkeypatch, *entry_points):
    monkeypatch.setattr(
        metadata, "entry_points", lambda *, group: entry_points if group == "haven.providers" else ()
    )


def test_list_provider_packages_with_nothing_installed_is_empty(monkeypatch):
    _patch_entry_points(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.list_provider_packages()
    assert result["ok"] is True
    assert result["providers"] == []


def test_list_provider_packages_shows_a_discovered_but_not_yet_installed_package(monkeypatch):
    ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.list_provider_packages()
    assert len(result["providers"]) == 1
    row = result["providers"][0]
    assert row["manifest"]["provider_id"] == "philips_hue"
    assert row["manifest"]["permissions"] == ["network access to your Hue bridge"]
    assert row["installed"] is False
    assert row["enabled"] is False
    assert row["active"] is False


def test_list_provider_packages_defaults_operational_profile_conservatively(monkeypatch):
    ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.list_provider_packages()
    operational = result["providers"][0]["manifest"]["operational"]
    assert operational == {
        "discovery": False,
        "observation": False,
        "read": False,
        "mutation": False,
        "webhook_push": False,
        "required_credential_scopes": [],
        "destructive_action_classes": [],
        "offline_behavior": None,
        "refresh_strategy": None,
    }


def test_list_provider_packages_surfaces_a_declared_operational_profile(monkeypatch):
    ep = metadata.EntryPoint(name="fake_mail", value=f"{__name__}:FAKE_COMMS_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.list_provider_packages()
    operational = result["providers"][0]["manifest"]["operational"]
    assert operational["observation"] is True
    assert operational["read"] is True
    assert operational["mutation"] is True
    assert operational["required_credential_scopes"] == ["mail.read", "mail.send"]
    assert operational["destructive_action_classes"] == ["reversible_external"]
    assert operational["offline_behavior"] == "queues outbound mail locally until reachable again"
    assert operational["refresh_strategy"] == "poll:60s"


def test_list_provider_packages_reports_a_broken_package_without_crashing(monkeypatch):
    ep = metadata.EntryPoint(name="broken", value=f"{__name__}:_BrokenPlugin", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.list_provider_packages()
    assert result["ok"] is True
    assert "error" in result["providers"][0]
    assert "manifest" not in result["providers"][0]


def test_install_provider_package_activates_it_without_touching_provider_kind(monkeypatch):
    """Regression: installing a community provider must never overwrite
    `provider_kind` -- that used to silently unplug Home Assistant's own
    wiring on the next rebuild once a second provider was installed (see
    `is_real_installation` in `haven/web/provider_install.py`)."""

    ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        service = _service(data_dir)
        result = service.install_provider_package(entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"})
        assert result["ok"] is True
        assert result["provider_id"] == "philips_hue"

        store = SetupConfigStore(data_dir / "haven.json")
        assert store.load().provider_kind is None
        installed = find_installed_provider(store, "philips_hue")
        assert installed is not None
        assert installed.enabled is True
        assert load_installed_provider_config(store, "philips_hue") == {"bridge_ip": "10.0.0.5"}


def test_install_provider_package_surfaces_a_build_failure_without_persisting(monkeypatch):
    ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        service = _service(data_dir)
        result = service.install_provider_package(entry_point_name="philips_hue", config={})
        assert result["ok"] is False
        assert "bridge_ip" in result["error"]

        store = SetupConfigStore(data_dir / "haven.json")
        assert store.load().provider_kind is None
        assert find_installed_provider(store, "philips_hue") is None


def test_install_provider_package_requires_a_known_entry_point(monkeypatch):
    _patch_entry_points(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.install_provider_package(entry_point_name="nonexistent", config={})
    assert result["ok"] is False
    assert "nonexistent" in result["error"]


def test_set_provider_package_enabled_toggles_without_losing_config(monkeypatch):
    ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, ep)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        service = _service(data_dir)
        service.install_provider_package(entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"})

        result = service.set_provider_package_enabled(provider_id="philips_hue", enabled=False)
        assert result["ok"] is True

        store = SetupConfigStore(data_dir / "haven.json")
        installed = find_installed_provider(store, "philips_hue")
        assert installed.enabled is False
        assert load_installed_provider_config(store, "philips_hue") == {"bridge_ip": "10.0.0.5"}


def test_set_provider_package_enabled_requires_it_to_be_installed(monkeypatch):
    _patch_entry_points(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        result = service.set_provider_package_enabled(provider_id="philips_hue", enabled=True)
    assert result["ok"] is False


def test_uninstall_provider_package_never_touches_provider_kind_or_other_providers(monkeypatch):
    """Regression: uninstalling one provider must only remove its own
    `installed_providers.json` entry -- never `provider_kind` (which
    `install_provider_package` no longer sets at all, see
    `is_real_installation`) and never another provider's entry."""

    hue_ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    _patch_entry_points(monkeypatch, hue_ep)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        service = _service(data_dir)
        service.install_provider_package(entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"})
        save_installed_provider(
            SetupConfigStore(data_dir / "haven.json"),
            provider_id="matter_bridge",
            entry_point_name="matter",
            config={},
        )

        result = service.uninstall_provider_package(provider_id="philips_hue")
        assert result["ok"] is True

        store = SetupConfigStore(data_dir / "haven.json")
        assert store.load().provider_kind is None
        assert find_installed_provider(store, "philips_hue") is None
        assert find_installed_provider(store, "matter_bridge") is not None
