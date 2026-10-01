"""Installable Ring simulator plugin.

Only the simulator is built in. A live provider must be a separately
reviewed transport that implements ``RingEventSource`` and uses HAVEN's
credential boundary; this plugin intentionally refuses a live mode instead
of accepting a token it cannot safely use.
"""

from __future__ import annotations

from typing import Mapping

from haven.core.consequence import ConsequenceClass
from haven.providers.plugin import (
    HavenProviderPlugin,
    ProviderConfigField,
    ProviderManifest,
    ProviderOperationalProfile,
)

from .provider import RingEvidenceProvider, RingSimulator


class RingSimulatorPlugin(HavenProviderPlugin):
    def describe(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="haven.ring.simulator",
            kind="observation",
            capabilities=frozenset({"ring_events", "context_observation"}),
            display_name="Ring evidence simulator",
            description=(
                "Deterministic local doorbell and motion evidence for proof runs; "
                "no Ring account, network, media, or actuation is included."
            ),
            permissions=("ring.events.read",),
            version="0.1.0",
            config_fields=(
                ProviderConfigField(
                    name="mode",
                    label="Transport mode (simulator only)",
                    required=False,
                    secret=False,
                ),
            ),
            operational=ProviderOperationalProfile(
                observation=True,
                read=True,
                required_credential_scopes=("ring.events.read",),
                destructive_action_classes=(ConsequenceClass.READ_ONLY,),
                offline_behavior="Deterministic local events continue without network access.",
                refresh_strategy="Pull the bounded event batch when HAVEN observes.",
            ),
        )

    def build(self, *, config: Mapping[str, str]) -> RingEvidenceProvider:
        mode = str(config.get("mode", "simulator")).strip().lower()
        if mode != "simulator":
            raise ValueError("the built-in Ring provider supports mode='simulator' only")
        return RingEvidenceProvider(RingSimulator())


PLUGIN = RingSimulatorPlugin()

__all__ = ["PLUGIN", "RingSimulatorPlugin"]
