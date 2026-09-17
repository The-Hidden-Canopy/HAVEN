import pytest

from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.providers import (
    CapabilityRegistry,
    ProviderCapabilities,
    UnknownProvider,
    build_default_registry,
)
from haven.providers.defaults import INTELLIGENCE_KIND


def test_register_and_lookup_by_provider_id():
    registry = CapabilityRegistry()
    provider = ScriptedIntelligenceProvider()
    registry.register(
        provider,
        capabilities=ProviderCapabilities(
            provider_id="test.intelligence",
            kind=INTELLIGENCE_KIND,
            capabilities=("interpret",),
        ),
    )

    assert registry.is_available("test.intelligence")
    assert registry.get("test.intelligence") is provider
    assert registry.capabilities_of("test.intelligence").supports("interpret")


def test_unknown_provider_raises_on_get_and_capabilities():
    registry = CapabilityRegistry()

    with pytest.raises(UnknownProvider):
        registry.get("nope")
    with pytest.raises(UnknownProvider):
        registry.capabilities_of("nope")
    assert registry.is_available("nope") is False


def test_find_filters_by_kind_and_required_capabilities():
    registry = CapabilityRegistry()
    registry.register(
        object(),
        capabilities=ProviderCapabilities(
            provider_id="interpret.only",
            kind=INTELLIGENCE_KIND,
            capabilities=("interpret",),
        ),
    )
    registry.register(
        object(),
        capabilities=ProviderCapabilities(
            provider_id="chat.and.vision",
            kind=INTELLIGENCE_KIND,
            capabilities=("chat", "vision"),
        ),
    )
    registry.register(
        object(),
        capabilities=ProviderCapabilities(
            provider_id="other.kind",
            kind="speech",
            capabilities=("interpret", "vision"),
        ),
    )

    assert registry.find(kind=INTELLIGENCE_KIND, requires=("chat",)) == ("chat.and.vision",)
    assert set(registry.find(kind=INTELLIGENCE_KIND)) == {"interpret.only", "chat.and.vision"}
    assert registry.find(kind="speech", requires=("interpret", "vision")) == ("other.kind",)


def test_capabilities_require_non_empty_identity():
    with pytest.raises(ValueError):
        ProviderCapabilities(provider_id="", kind=INTELLIGENCE_KIND, capabilities=())
    with pytest.raises(ValueError):
        ProviderCapabilities(provider_id="x", kind="", capabilities=())


def test_default_registry_exposes_the_fixture_intelligence_provider():
    registry = build_default_registry()

    found = registry.find(kind=INTELLIGENCE_KIND, requires=("interpret",))
    assert found == ("haven.fixture_intelligence",)
    assert isinstance(registry.get(found[0]), ScriptedIntelligenceProvider)
