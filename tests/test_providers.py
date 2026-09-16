import pytest

from haven.intelligence.gateway import FixtureModelGateway
from haven.providers import (
    CapabilityRegistry,
    ProviderCapabilities,
    UnknownProvider,
    build_default_registry,
)
from haven.providers.defaults import MODEL_GATEWAY_KIND


def test_register_and_lookup_by_provider_id():
    registry = CapabilityRegistry()
    gateway = FixtureModelGateway()
    registry.register(
        gateway,
        capabilities=ProviderCapabilities(
            provider_id="test.gateway",
            kind=MODEL_GATEWAY_KIND,
            capabilities=("rule_interpretation",),
        ),
    )

    assert registry.is_available("test.gateway")
    assert registry.get("test.gateway") is gateway
    assert registry.capabilities_of("test.gateway").supports("rule_interpretation")


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
            provider_id="chat.only",
            kind=MODEL_GATEWAY_KIND,
            capabilities=("rule_interpretation",),
        ),
    )
    registry.register(
        object(),
        capabilities=ProviderCapabilities(
            provider_id="chat.and.vision",
            kind=MODEL_GATEWAY_KIND,
            capabilities=("rule_interpretation", "vision"),
        ),
    )
    registry.register(
        object(),
        capabilities=ProviderCapabilities(
            provider_id="other.kind",
            kind="speech",
            capabilities=("rule_interpretation", "vision"),
        ),
    )

    assert registry.find(kind=MODEL_GATEWAY_KIND, requires=("vision",)) == ("chat.and.vision",)
    assert set(registry.find(kind=MODEL_GATEWAY_KIND)) == {"chat.only", "chat.and.vision"}
    assert registry.find(kind="speech", requires=("rule_interpretation", "vision")) == ("other.kind",)


def test_capabilities_require_non_empty_identity():
    with pytest.raises(ValueError):
        ProviderCapabilities(provider_id="", kind=MODEL_GATEWAY_KIND, capabilities=())
    with pytest.raises(ValueError):
        ProviderCapabilities(provider_id="x", kind="", capabilities=())


def test_default_registry_exposes_the_fixture_gateway():
    registry = build_default_registry()

    found = registry.find(kind=MODEL_GATEWAY_KIND, requires=("rule_interpretation",))
    assert found == ("haven.fixture_model_gateway",)
    assert isinstance(registry.get(found[0]), FixtureModelGateway)
