"""Provider package discovery/inspection/build through `importlib.metadata`
entry points -- the mechanism that lets a household `pip install` a
community provider and have HAVEN find it, without Haven Core importing
anything until a caller explicitly asks.

`importlib.metadata.EntryPoint.load()` really does `import_module(...)` plus
attribute traversal, so these tests point entry points at real objects
defined in this file (importable as `test_providers_loader:NAME` under
pytest's default "prepend" import mode) rather than mocking the import
machinery itself -- exercising the real mechanism end to end.
"""

from __future__ import annotations

from importlib import metadata
from typing import Mapping

import pytest

from haven.providers.loader import (
    ProviderLoadError,
    build_provider,
    discover_provider_packages,
    inspect_provider_package,
)
from haven.providers.plugin import ProviderConfigField, ProviderManifest

_MODULE = __name__


class _FakeHueProvider:
    def __init__(self, config: Mapping[str, str]) -> None:
        self.config = dict(config)


class FakeHuePlugin:
    """A well-formed plugin, importable as `test_providers_loader:FakeHuePlugin`."""

    def describe(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="philips_hue",
            kind="execution",
            capabilities=frozenset({"light.turn_on", "light.turn_off"}),
            display_name="Philips Hue",
            description="Controls Hue lights over your local bridge.",
            permissions=("network access to your Hue bridge on the local network",),
            version="1.0.0",
            config_fields=(
                ProviderConfigField(name="bridge_ip", label="Bridge IP address"),
                ProviderConfigField(name="api_key", label="Bridge API key", secret=True),
            ),
        )

    def build(self, *, config: Mapping[str, str]) -> object:
        if "bridge_ip" not in config:
            raise ValueError("bridge_ip is required")
        return _FakeHueProvider(config)


FAKE_HUE_PLUGIN_INSTANCE = FakeHuePlugin()


class _NotAPlugin:
    """Missing describe()/build() -- a malformed entry point target."""


class _BrokenDescribePlugin:
    def describe(self):
        return {"not": "a ProviderManifest"}

    def build(self, *, config: Mapping[str, str]) -> object:
        return object()


def _entry_point(name: str, attr: str) -> metadata.EntryPoint:
    return metadata.EntryPoint(name=name, value=f"{_MODULE}:{attr}", group="haven.providers")


def _discovered(name: str, attr: str):
    from haven.providers.loader import DiscoveredProviderPackage

    return DiscoveredProviderPackage(
        entry_point_name=name,
        distribution_name="haven-philips-hue",
        distribution_version="1.0.0",
        _entry_point=_entry_point(name, attr),
    )


def test_discover_provider_packages_reads_the_haven_providers_group(monkeypatch):
    fake_eps = (_entry_point("philips_hue", "FAKE_HUE_PLUGIN_INSTANCE"),)
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: fake_eps if group == "haven.providers" else ())

    discovered = discover_provider_packages()
    assert len(discovered) == 1
    assert discovered[0].entry_point_name == "philips_hue"


def test_discover_returns_empty_tuple_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: ())
    assert discover_provider_packages() == ()


def test_inspect_provider_package_returns_the_manifest_without_building():
    discovered = _discovered("philips_hue", "FAKE_HUE_PLUGIN_INSTANCE")
    manifest = inspect_provider_package(discovered)
    assert manifest.provider_id == "philips_hue"
    assert manifest.kind == "execution"
    assert "light.turn_on" in manifest.capabilities
    assert len(manifest.config_fields) == 2
    assert manifest.config_fields[1].secret is True


def test_build_provider_constructs_the_real_instance_with_config():
    discovered = _discovered("philips_hue", "FAKE_HUE_PLUGIN_INSTANCE")
    manifest, instance = build_provider(discovered, config={"bridge_ip": "10.0.0.5", "api_key": "secret"})
    assert manifest.provider_id == "philips_hue"
    assert isinstance(instance, _FakeHueProvider)
    assert instance.config == {"bridge_ip": "10.0.0.5", "api_key": "secret"}


def test_build_provider_propagates_a_provider_specific_config_error():
    discovered = _discovered("philips_hue", "FAKE_HUE_PLUGIN_INSTANCE")
    with pytest.raises(ValueError, match="bridge_ip"):
        build_provider(discovered, config={})


def test_build_provider_defaults_to_empty_config():
    discovered = _discovered("philips_hue", "FAKE_HUE_PLUGIN_INSTANCE")
    with pytest.raises(ValueError):
        build_provider(discovered)


def test_entry_point_resolving_to_a_class_is_instantiated():
    discovered = _discovered("philips_hue", "FakeHuePlugin")
    manifest = inspect_provider_package(discovered)
    assert manifest.provider_id == "philips_hue"


def test_malformed_plugin_missing_describe_or_build_raises_provider_load_error():
    discovered = _discovered("broken", "_NotAPlugin")
    with pytest.raises(ProviderLoadError, match="describe"):
        inspect_provider_package(discovered)


def test_plugin_that_does_not_return_a_manifest_raises_provider_load_error():
    discovered = _discovered("broken", "_BrokenDescribePlugin")
    with pytest.raises(ProviderLoadError, match="ProviderManifest"):
        inspect_provider_package(discovered)


def test_unimportable_module_raises_provider_load_error():
    from haven.providers.loader import DiscoveredProviderPackage

    bad_entry_point = metadata.EntryPoint(
        name="ghost", value="this.module.does.not.exist:Thing", group="haven.providers"
    )
    discovered = DiscoveredProviderPackage(
        entry_point_name="ghost",
        distribution_name=None,
        distribution_version=None,
        _entry_point=bad_entry_point,
    )
    with pytest.raises(ProviderLoadError):
        inspect_provider_package(discovered)
