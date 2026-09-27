"""ProviderOperationalProfile (native product-consolidation plan, P1
"Providers" §5.2): operational capability metadata on `ProviderManifest`,
defaulting conservatively so a provider written before this profile existed
needs no change and never overclaims a capability it never declared."""

from __future__ import annotations

import pytest

from haven.core.consequence import ConsequenceClass
from haven.providers.plugin import ProviderManifest, ProviderOperationalProfile


def test_defaults_are_the_most_conservative_shape():
    profile = ProviderOperationalProfile()
    assert profile.discovery is False
    assert profile.observation is False
    assert profile.read is False
    assert profile.mutation is False
    assert profile.webhook_push is False
    assert profile.required_credential_scopes == ()
    assert profile.destructive_action_classes == ()
    assert profile.offline_behavior is None
    assert profile.refresh_strategy is None


def test_a_provider_manifest_written_before_this_profile_existed_still_works():
    manifest = ProviderManifest(
        provider_id="p",
        kind="k",
        capabilities=frozenset({"light.turn_on"}),
        display_name="P",
        description="d",
    )
    assert manifest.operational == ProviderOperationalProfile()


def test_declares_real_operational_metadata():
    profile = ProviderOperationalProfile(
        discovery=True,
        observation=True,
        mutation=True,
        required_credential_scopes=("scope-a", "scope-b"),
        destructive_action_classes=(ConsequenceClass.HIGH_IMPACT, ConsequenceClass.DESTRUCTIVE),
        offline_behavior="last-known state only",
        refresh_strategy="webhook",
    )
    assert profile.discovery is True
    assert profile.required_credential_scopes == ("scope-a", "scope-b")
    assert profile.destructive_action_classes == (ConsequenceClass.HIGH_IMPACT, ConsequenceClass.DESTRUCTIVE)


def test_destructive_action_classes_must_be_consequence_class_values():
    with pytest.raises(ValueError):
        ProviderOperationalProfile(destructive_action_classes=("high_impact",))  # a raw string, not the enum


def test_manifest_rejects_a_non_profile_operational_value():
    with pytest.raises(ValueError):
        ProviderManifest(
            provider_id="p",
            kind="k",
            capabilities=frozenset(),
            display_name="P",
            description="d",
            operational="not a profile",  # type: ignore[arg-type]
        )
