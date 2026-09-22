"""PluginDescriptor is the closed, minimal contract for one catalog entry."""

import pytest

from haven.plugins import (
    InvalidPluginIdError,
    PluginCapability,
    PluginDataBoundary,
    PluginDescriptor,
    PluginStatus,
    safe_plugin_id,
)


def _descriptor(**overrides) -> PluginDescriptor:
    defaults = dict(
        plugin_id="traceglass",
        display_name="TraceGlass",
        publisher="The Hidden Canopy LLC",
        capability=PluginCapability.DECISION_CHAIN_RECONSTRUCTION,
        status=PluginStatus.CATALOG_ONLY,
        data_boundary=PluginDataBoundary.EXPORTED_RECEIPTS_ONLY,
        description="Reconstructs decision chains from exported receipts.",
    )
    defaults.update(overrides)
    return PluginDescriptor(**defaults)


def test_capability_and_status_enums_are_closed_and_stable():
    assert PluginCapability("decision_chain_reconstruction") is PluginCapability.DECISION_CHAIN_RECONSTRUCTION
    assert PluginCapability("adaptive_curriculum_evaluation") is PluginCapability.ADAPTIVE_CURRICULUM_EVALUATION
    assert PluginStatus.CATALOG_ONLY.value == "catalog_only"
    assert PluginDataBoundary.EXPORTED_RECEIPTS_ONLY.value == "exported_receipts_only"


def test_accepts_enums_as_plain_strings():
    descriptor = _descriptor(capability="adaptive_curriculum_evaluation", status="supported")
    assert descriptor.capability is PluginCapability.ADAPTIVE_CURRICULUM_EVALUATION
    assert descriptor.status is PluginStatus.SUPPORTED


def test_unknown_capability_is_rejected():
    with pytest.raises(ValueError):
        _descriptor(capability="arbitrary_capability")


def test_unknown_data_boundary_is_rejected():
    with pytest.raises(ValueError):
        _descriptor(data_boundary="live_authority_access")


def test_blank_description_or_names_are_rejected():
    for field in ("display_name", "publisher", "description"):
        with pytest.raises(ValueError):
            _descriptor(**{field: "   "})


def test_overlong_description_is_rejected():
    with pytest.raises(ValueError):
        _descriptor(description="x" * 601)


@pytest.mark.parametrize(
    "candidate",
    ["Not Valid!", "-leading-dash", "has/slash", "has\\backslash", "..", "", "a" * 65],
)
def test_safe_plugin_id_rejects_unsafe_shapes(candidate):
    with pytest.raises(InvalidPluginIdError):
        safe_plugin_id(candidate)


def test_safe_plugin_id_accepts_the_reference_plugin_ids():
    assert safe_plugin_id("traceglass") == "traceglass"
    assert safe_plugin_id("ghost-teacher") == "ghost-teacher"


def test_safe_plugin_id_rejects_non_string():
    with pytest.raises(InvalidPluginIdError):
        safe_plugin_id(123)  # type: ignore[arg-type]
