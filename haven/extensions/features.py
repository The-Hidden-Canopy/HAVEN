"""Discovery and attachment boundary for HAVEN Feature Modules.

Feature modules run inside HAVEN, but they receive a catalog of explicit
application-service operations rather than stores, credentials, or a generic
director object. Discovery reads entry-point metadata only; importing and
attaching a module is always an explicit composition-root decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .taxonomy import ExtensionClass, ExtensionDescriptor


_ENTRY_POINT_GROUP = "haven.features"


class FeatureLoadError(RuntimeError):
    """A feature entry point is malformed or could not be attached."""


@dataclass(frozen=True)
class FeatureServiceCatalog:
    """The only application-facing capability a feature module receives.

    Each value is an explicit application-service operation supplied by the
    composition root. The catalog never stores a raw domain store, and an
    unknown operation fails closed before any callback is invoked.
    """

    operations: Mapping[str, Callable[..., Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.operations, Mapping):
            raise ValueError("feature service operations must be a mapping")
        copied: dict[str, Callable[..., Any]] = {}
        for name, operation in self.operations.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("feature service operation names must be non-empty strings")
            if not callable(operation):
                raise ValueError(f"feature service operation {name!r} must be callable")
            copied[name.strip()] = operation
        object.__setattr__(self, "operations", MappingProxyType(copied))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.operations))

    def call(self, name: str, **params: Any) -> Any:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("feature service operation name must be non-empty")
        operation = self.operations.get(name.strip())
        if operation is None:
            raise ValueError(f"unknown feature service operation: {name!r}")
        return operation(**params)


class FeatureModule(Protocol):
    def attach(self, *, services: FeatureServiceCatalog) -> object: ...


class FeatureModulePlugin(Protocol):
    def describe(self) -> ExtensionDescriptor: ...

    def build(self) -> FeatureModule: ...


@dataclass(frozen=True)
class DiscoveredFeatureModule:
    """Metadata for one ``haven.features`` entry point; no import has run."""

    entry_point_name: str
    distribution_name: str | None
    distribution_version: str | None
    _entry_point: metadata.EntryPoint = field(repr=False, compare=False)

    def load_plugin(self) -> FeatureModulePlugin:
        try:
            target = self._entry_point.load()
        except Exception as exc:
            raise FeatureLoadError(
                f"could not import feature module for entry point {self.entry_point_name!r}: {exc}"
            ) from exc
        plugin = target() if isinstance(target, type) else target
        if not callable(getattr(plugin, "describe", None)) or not callable(getattr(plugin, "build", None)):
            raise FeatureLoadError(
                f"entry point {self.entry_point_name!r} does not implement FeatureModulePlugin "
                "(missing describe()/build())"
            )
        return plugin


def discover_feature_modules() -> tuple[DiscoveredFeatureModule, ...]:
    """Read installed feature metadata without importing feature code."""

    discovered: list[DiscoveredFeatureModule] = []
    for entry_point in metadata.entry_points(group=_ENTRY_POINT_GROUP):
        dist = entry_point.dist
        discovered.append(
            DiscoveredFeatureModule(
                entry_point_name=entry_point.name,
                distribution_name=dist.name if dist is not None else None,
                distribution_version=dist.version if dist is not None else None,
                _entry_point=entry_point,
            )
        )
    return tuple(discovered)


def _descriptor(discovered: DiscoveredFeatureModule, plugin: FeatureModulePlugin) -> ExtensionDescriptor:
    try:
        descriptor = plugin.describe()
    except Exception as exc:
        raise FeatureLoadError(f"{discovered.entry_point_name!r}.describe() raised: {exc}") from exc
    if not isinstance(descriptor, ExtensionDescriptor):
        raise FeatureLoadError(f"{discovered.entry_point_name!r}.describe() did not return an ExtensionDescriptor")
    if descriptor.extension_class is not ExtensionClass.FEATURE:
        raise FeatureLoadError(
            f"{discovered.entry_point_name!r} declared extension class "
            f"{descriptor.extension_class.value!r}, expected 'feature'"
        )
    return descriptor


def inspect_feature_module(discovered: DiscoveredFeatureModule) -> ExtensionDescriptor:
    """Import a discovered module only to inspect its declared boundary."""

    return _descriptor(discovered, discovered.load_plugin())


def build_feature_module(
    discovered: DiscoveredFeatureModule, *, services: FeatureServiceCatalog
) -> tuple[ExtensionDescriptor, FeatureModule]:
    """Build and attach one approved feature through explicit service calls."""

    plugin = discovered.load_plugin()
    descriptor = _descriptor(discovered, plugin)
    try:
        module = plugin.build()
    except Exception as exc:
        raise FeatureLoadError(f"{discovered.entry_point_name!r}.build() raised: {exc}") from exc
    if not callable(getattr(module, "attach", None)):
        raise FeatureLoadError(f"{discovered.entry_point_name!r}.build() returned a module without attach()")
    try:
        module.attach(services=services)
    except Exception as exc:
        raise FeatureLoadError(f"{discovered.entry_point_name!r}.attach() raised: {exc}") from exc
    return descriptor, module


__all__ = [
    "DiscoveredFeatureModule",
    "FeatureLoadError",
    "FeatureModule",
    "FeatureModulePlugin",
    "FeatureServiceCatalog",
    "build_feature_module",
    "discover_feature_modules",
    "inspect_feature_module",
]
