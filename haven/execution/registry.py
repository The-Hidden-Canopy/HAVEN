"""The execution-adapter contracts and device registry keyed by provider_id.

`HavenRuntime` already has one caller of this: `_execution_adapter_for()`
resolves a target device's `DeviceManifest.provider_id` (from
`AuthorityEngine.device_registry`) and looks the adapter up here, falling
back to a single default adapter (`home_assistant`) only when this registry
was never given one for a device -- so a deployment that has not opted into
multi-provider routing sees no behavior change at all.

`ProviderExecutionAdapter` is the additive open-vocabulary seam for
computer/life providers. It intentionally does not change the existing
device registry or force legacy integrations to accept a new command shape.
"""

from __future__ import annotations

from typing import Protocol

from haven.core.domain import DeviceCommand, DeviceResult

from .commands import ProviderCommand, ProviderResult


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class ExecutionAdapter(Protocol):
    def execute(self, command: DeviceCommand) -> DeviceResult:
        """Execute one already-authorized command."""


class ProviderExecutionAdapter(Protocol):
    def execute_provider(self, command: ProviderCommand) -> ProviderResult:
        """Execute one already-authorized provider capability request."""


class UnknownExecutionProvider(KeyError):
    """Raised when a provider_id has no registered execution adapter."""


class ExecutionProviderRegistry:
    """Maps a device manifest's provider_id to the adapter that executes for it.

    This is a straight 1:1 lookup, not a capability negotiation: a device
    names exactly one `provider_id`, so there is exactly one adapter to find,
    unlike `haven.providers.CapabilityRegistry`, which answers "which of
    several registered providers satisfies this capability."
    """

    def __init__(self) -> None:
        self._adapters: dict[str, ExecutionAdapter] = {}

    def register(self, provider_id: str, adapter: ExecutionAdapter) -> None:
        self._adapters[_require_text(provider_id, name="provider_id")] = adapter

    def is_registered(self, provider_id: str) -> bool:
        return provider_id in self._adapters

    def get(self, provider_id: str) -> ExecutionAdapter:
        try:
            return self._adapters[provider_id]
        except KeyError:
            raise UnknownExecutionProvider(provider_id) from None


__all__ = [
    "ExecutionAdapter",
    "ExecutionProviderRegistry",
    "ProviderExecutionAdapter",
    "UnknownExecutionProvider",
]
