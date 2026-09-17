"""A capability registry, not a model catalog.

Haven core is meant to ask "is there a provider of kind X that supports
capability Y", never "is IDA installed". Keeping the lookup shaped that way
is what lets a free provider (a fixture, Ollama, a local gguf model) and a
licensed one (a Hidden Canopy model) sit behind the same boundary: this
module has no notion of price, license tier, or vendor beyond the opaque
provider_id a plugin registers itself under.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class UnknownProvider(KeyError):
    """Raised when a provider_id has not been registered."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider declares it can do, independent of its identity."""

    provider_id: str
    kind: str
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _require_text(self.provider_id, name="provider_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="kind"))
        object.__setattr__(
            self,
            "capabilities",
            frozenset(_require_text(item, name="capability") for item in self.capabilities),
        )

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def supports_all(self, capabilities: Iterable[str]) -> bool:
        return set(capabilities).issubset(self.capabilities)


class CapabilityRegistry:
    """Tracks which providers are available and what each one supports.

    This is deliberately the whole contract: register an instance under a
    kind and a set of capability names, then look it up by either. There is
    no availability probing, no license check, and no ranking between
    providers -- a caller that needs more than "does one exist" builds that
    on top, outside this registry.
    """

    def __init__(self) -> None:
        self._instances: dict[str, object] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}

    def register(self, instance: object, *, capabilities: ProviderCapabilities) -> None:
        self._instances[capabilities.provider_id] = instance
        self._capabilities[capabilities.provider_id] = capabilities

    def is_available(self, provider_id: str) -> bool:
        return provider_id in self._instances

    def capabilities_of(self, provider_id: str) -> ProviderCapabilities:
        try:
            return self._capabilities[provider_id]
        except KeyError:
            raise UnknownProvider(provider_id) from None

    def get(self, provider_id: str) -> object:
        try:
            return self._instances[provider_id]
        except KeyError:
            raise UnknownProvider(provider_id) from None

    def registered(self) -> tuple[ProviderCapabilities, ...]:
        """Every registered capability declaration, in registration order.

        This is the honest "what is HAVEN made of right now" view: callers
        that render or audit the provider set enumerate it here rather than
        re-declaring it, so extra registrations show up automatically.
        """

        return tuple(self._capabilities.values())

    def find(self, *, kind: str, requires: Iterable[str] = ()) -> tuple[str, ...]:
        """Return provider_ids of the given kind that satisfy every requirement."""

        required = frozenset(requires)
        return tuple(
            provider_id
            for provider_id, caps in self._capabilities.items()
            if caps.kind == kind and caps.supports_all(required)
        )


__all__ = ["CapabilityRegistry", "ProviderCapabilities", "UnknownProvider"]
