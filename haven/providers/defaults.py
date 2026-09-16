"""Wiring for the providers that ship with Haven itself.

Nothing here is required: a deployment can build its own CapabilityRegistry
and register only the providers it has. This module exists so the fixture
slice has something to register without every caller re-declaring it.
"""

from __future__ import annotations

from haven.intelligence.gateway import FixtureModelGateway

from .capabilities import CapabilityRegistry, ProviderCapabilities

MODEL_GATEWAY_KIND = "model_gateway"


def build_default_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        FixtureModelGateway(),
        capabilities=ProviderCapabilities(
            provider_id="haven.fixture_model_gateway",
            kind=MODEL_GATEWAY_KIND,
            capabilities=("rule_interpretation",),
        ),
    )
    return registry


__all__ = ["MODEL_GATEWAY_KIND", "build_default_registry"]
