"""The contract an installable HAVEN provider package publishes.

`haven/providers/capabilities.py` answers "is a provider of kind X
available" once something is already registered; this module is what gets a
provider registered in the first place without a contributor ever touching
Haven Core -- the missing piece between "I wrote a `DiscoveryProvider`
implementation" (`docs/authoring-providers.md`) and "a household can
actually install it." A provider package publishes one object -- a module
level singleton, or a zero-argument class -- implementing `describe()`/
`build()` below, and declares it under the `haven.providers` entry-point
group (see `haven/providers/loader.py` for how HAVEN finds it, and
`docs/authoring-providers.md` for the publishing walkthrough).

Two methods, deliberately not one: `describe()` is always safe to call --
it returns static metadata a household can review (what this needs, what
it can do) without constructing anything or touching a network, config
file, or credential. `build()` is the point a household has actually
consented -- it may open a connection, read a token, or fail if
`config` is missing something it required. This is the same
inspect-before-install shape `haven/models` already uses (a manifest is
read before any weights download), applied to providers instead of models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ProviderConfigField:
    """One piece of configuration a provider's `build()` needs, for a setup
    UI to render generically without hardcoding a single field's name.

    `secret` marks a value that must never be stored inline alongside
    ordinary configuration (the same treatment `home_assistant`'s access
    token already gets: a separate sidecar file, never a plain field in
    `haven.json`) -- a setup UI renders it as a password input and a
    persistence layer must keep it out of anything committed or logged.
    """

    name: str
    label: str
    required: bool = True
    secret: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, name="config field name"))
        object.__setattr__(self, "label", _require_text(self.label, name="config field label"))


@dataclass(frozen=True)
class ProviderManifest:
    """A provider package's self-description -- safe to read before install."""

    provider_id: str
    kind: str
    capabilities: frozenset[str]
    display_name: str
    description: str
    permissions: tuple[str, ...] = ()
    version: str = "0.0.0"
    homepage: str | None = None
    config_fields: tuple[ProviderConfigField, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _require_text(self.provider_id, name="provider_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        object.__setattr__(
            self, "capabilities", frozenset(_require_text(c, name="capability") for c in self.capabilities)
        )
        object.__setattr__(self, "display_name", _require_text(self.display_name, name="display_name"))
        object.__setattr__(self, "description", _require_text(self.description, name="description"))
        object.__setattr__(self, "permissions", tuple(self.permissions))
        object.__setattr__(self, "config_fields", tuple(self.config_fields))


class HavenProviderPlugin(Protocol):
    """What a `haven.providers` entry point must resolve to.

    `build()` always receives a `config` mapping (empty if the provider
    declared no `config_fields`) rather than `**kwargs`, so the loader's
    calling convention never depends on what a specific provider needs.
    """

    def describe(self) -> ProviderManifest:
        """Static self-description. Must not construct anything real."""

    def build(self, *, config: Mapping[str, str]) -> object:
        """Construct the real provider instance a household just approved."""


__all__ = ["HavenProviderPlugin", "ProviderConfigField", "ProviderManifest"]
