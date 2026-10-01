"""Feature Module discovery, service-only attachment, and fail-closed loading."""

from __future__ import annotations

from importlib import metadata

import pytest

from haven.extensions import (
    ExtensionClass,
    ExtensionDescriptor,
    FeatureLoadError,
    FeatureServiceCatalog,
    build_feature_module,
    discover_feature_modules,
    inspect_feature_module,
)
from haven.extensions.features import DiscoveredFeatureModule


_MODULE = __name__


class _Feature:
    def __init__(self) -> None:
        self.services = None

    def attach(self, *, services: FeatureServiceCatalog) -> object:
        self.services = services
        services.call("tasks.list", visible=("scope:personal",))
        return self


class FeaturePlugin:
    def describe(self) -> ExtensionDescriptor:
        return ExtensionDescriptor(
            extension_id="feature.test",
            display_name="Test feature",
            extension_class=ExtensionClass.FEATURE,
            source="test",
        )

    def build(self) -> _Feature:
        return _Feature()


FEATURE_PLUGIN = FeaturePlugin()


class WrongClassPlugin(FeaturePlugin):
    def describe(self) -> ExtensionDescriptor:
        return ExtensionDescriptor(
            extension_id="intelligence.wrong",
            display_name="Wrong class",
            extension_class=ExtensionClass.INTELLIGENCE,
            source="test",
        )


class MalformedPlugin:
    def describe(self) -> ExtensionDescriptor:
        return FeaturePlugin().describe()


def _entry_point(name: str, attr: str) -> metadata.EntryPoint:
    return metadata.EntryPoint(name=name, value=f"{_MODULE}:{attr}", group="haven.features")


def _discovered(name: str, attr: str) -> DiscoveredFeatureModule:
    return DiscoveredFeatureModule(
        entry_point_name=name,
        distribution_name="haven-feature-test",
        distribution_version="1.0.0",
        _entry_point=_entry_point(name, attr),
    )


def test_feature_discovery_reads_metadata_only(monkeypatch):
    fake_eps = (_entry_point("test_feature", "FEATURE_PLUGIN"),)
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: fake_eps if group == "haven.features" else ())
    discovered = discover_feature_modules()
    assert len(discovered) == 1
    assert discovered[0].entry_point_name == "test_feature"


def test_feature_inspection_requires_the_feature_extension_class():
    descriptor = inspect_feature_module(_discovered("test_feature", "FEATURE_PLUGIN"))
    assert descriptor.extension_class is ExtensionClass.FEATURE
    with pytest.raises(FeatureLoadError, match="expected 'feature'"):
        inspect_feature_module(_discovered("wrong", "WrongClassPlugin"))


def test_feature_build_receives_only_explicit_application_operations():
    calls: list[tuple[str, tuple[str, ...]]] = []
    services = FeatureServiceCatalog(
        operations={
            "tasks.list": lambda *, visible: calls.append(("tasks.list", visible)) or {"ok": True},
        }
    )
    descriptor, module = build_feature_module(
        _discovered("test_feature", "FEATURE_PLUGIN"), services=services
    )
    assert descriptor.extension_id == "feature.test"
    assert module.services is services
    assert calls == [("tasks.list", ("scope:personal",))]
    assert services.names == ("tasks.list",)


def test_feature_service_catalog_rejects_unknown_operations_and_non_callables():
    with pytest.raises(ValueError, match="callable"):
        FeatureServiceCatalog(operations={"tasks.list": object()})
    services = FeatureServiceCatalog()
    with pytest.raises(ValueError, match="unknown feature service operation"):
        services.call("stores.raw")


def test_malformed_feature_plugin_is_rejected():
    with pytest.raises(FeatureLoadError, match=r"missing describe\(\)/build\(\)"):
        inspect_feature_module(_discovered("malformed", "MalformedPlugin"))


def test_unimportable_feature_module_is_rejected():
    bad = metadata.EntryPoint(
        name="ghost", value="this.module.does.not.exist:Thing", group="haven.features"
    )
    discovered = DiscoveredFeatureModule(
        entry_point_name="ghost",
        distribution_name=None,
        distribution_version=None,
        _entry_point=bad,
    )
    with pytest.raises(FeatureLoadError):
        inspect_feature_module(discovered)
