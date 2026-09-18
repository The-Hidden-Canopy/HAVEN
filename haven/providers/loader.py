"""Discover, inspect, and build installed HAVEN provider packages.

Stdlib only: `importlib.metadata` entry points are how HAVEN finds a
provider package a household `pip install`ed, with zero dependency on any
plugin framework. A community port of Philips Hue, Google Home, Matter, or
anything else `docs/authoring-providers.md` covers ships as an ordinary
Python package declaring, in its own `pyproject.toml`:

```toml
[project.entry-points."haven.providers"]
philips_hue = "haven_philips_hue.plugin:PLUGIN"
```

where `PLUGIN` is a module-level object (or a zero-argument class) that
satisfies `HavenProviderPlugin` (`haven/providers/plugin.py`). Nothing in
Haven Core imports the provider package until a caller explicitly asks to
inspect or build one of its discovered entries -- `discover_provider_packages`
touches only package *metadata*, never the package's own code, which is
what makes it safe to run automatically (e.g. to populate a Settings ->
Providers list) rather than only on explicit user action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
from typing import Mapping

from .plugin import HavenProviderPlugin, ProviderManifest

_ENTRY_POINT_GROUP = "haven.providers"


class ProviderLoadError(RuntimeError):
    """A discovered provider package could not be imported, described, or built."""


@dataclass(frozen=True)
class DiscoveredProviderPackage:
    """One `haven.providers` entry point found on this Python environment.

    Metadata only -- constructing this never imports the provider package.
    `entry_point_name` is the name the package chose in its own
    `pyproject.toml` (e.g. `"philips_hue"`), not necessarily the eventual
    `provider_id` (that only becomes known once `describe()` is called).
    """

    entry_point_name: str
    distribution_name: str | None
    distribution_version: str | None
    _entry_point: metadata.EntryPoint = field(repr=False, compare=False)

    def load_plugin(self) -> HavenProviderPlugin:
        """Import the package and resolve its declared plugin object.

        The entry point may resolve to a plugin instance directly (a module-
        level singleton) or to a zero-argument class/callable that produces
        one; either is accepted so a provider author can use whichever is
        more natural for their package.
        """

        try:
            target = self._entry_point.load()
        except Exception as exc:
            raise ProviderLoadError(
                f"could not import provider package for entry point {self.entry_point_name!r}: {exc}"
            ) from exc
        plugin = target() if isinstance(target, type) else target
        if not callable(getattr(plugin, "describe", None)) or not callable(getattr(plugin, "build", None)):
            raise ProviderLoadError(
                f"entry point {self.entry_point_name!r} does not implement HavenProviderPlugin "
                "(missing describe()/build())"
            )
        return plugin


def discover_provider_packages() -> tuple[DiscoveredProviderPackage, ...]:
    """Every `haven.providers` entry point installed in this environment.

    Reads package metadata only; no provider package is imported by this
    call. Safe to call unconditionally (e.g. to render a Settings ->
    Providers list) without asking a household first.
    """

    discovered = []
    for entry_point in metadata.entry_points(group=_ENTRY_POINT_GROUP):
        dist = entry_point.dist
        discovered.append(
            DiscoveredProviderPackage(
                entry_point_name=entry_point.name,
                distribution_name=dist.name if dist is not None else None,
                distribution_version=dist.version if dist is not None else None,
                _entry_point=entry_point,
            )
        )
    return tuple(discovered)


def inspect_provider_package(discovered: DiscoveredProviderPackage) -> ProviderManifest:
    """Import the package and return its self-description, nothing built.

    This is the "ask what capabilities it declares, present permissions"
    step: a household can review `ProviderManifest.permissions` and
    `config_fields` before anything real is constructed.
    """

    plugin = discovered.load_plugin()
    manifest = plugin.describe()
    if not isinstance(manifest, ProviderManifest):
        raise ProviderLoadError(f"{discovered.entry_point_name!r}.describe() did not return a ProviderManifest")
    return manifest


def build_provider(
    discovered: DiscoveredProviderPackage, *, config: Mapping[str, str] | None = None
) -> tuple[ProviderManifest, object]:
    """Construct the real provider instance for a package a household approved.

    Registering the result into a `CapabilityRegistry` or
    `ExecutionProviderRegistry` is the caller's job -- this function only
    ever builds the instance, matching the existing "don't conflate the
    registries" boundary (`docs/authoring-providers.md`).
    """

    plugin = discovered.load_plugin()
    manifest = plugin.describe()
    instance = plugin.build(config=dict(config or {}))
    return manifest, instance


__all__ = [
    "DiscoveredProviderPackage",
    "ProviderLoadError",
    "build_provider",
    "discover_provider_packages",
    "inspect_provider_package",
]
