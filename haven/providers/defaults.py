"""Wiring for the providers that ship with Haven itself.

Nothing here is required: a deployment can build its own CapabilityRegistry
and register only the providers it has. This module exists so the fixture
slice has something to register without every caller re-declaring it.
"""

from __future__ import annotations

from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.speech import (
    EnergyVad,
    ScriptedAsrProvider,
    ScriptedSynthesizer,
    ScriptedWakeDetector,
)

from .capabilities import CapabilityRegistry, ProviderCapabilities

INTELLIGENCE_KIND = "intelligence"
WAKE_WORD_KIND = "wake_word"
VAD_KIND = "vad"
SPEECH_TO_TEXT_KIND = "speech_to_text"
TEXT_TO_SPEECH_KIND = "text_to_speech"


def build_default_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        ScriptedIntelligenceProvider(),
        capabilities=ProviderCapabilities(
            provider_id="haven.fixture_intelligence",
            kind=INTELLIGENCE_KIND,
            capabilities=("interpret", "chat", "propose_rule", "explain"),
        ),
    )
    # Scripted speech fixtures: pure Python, no audio hardware required,
    # so the default registry stays importable anywhere core does.
    registry.register(
        ScriptedWakeDetector(),
        capabilities=ProviderCapabilities(
            provider_id="haven.fixture_wake_word",
            kind=WAKE_WORD_KIND,
            capabilities=("preroll",),
        ),
    )
    registry.register(
        EnergyVad(),
        capabilities=ProviderCapabilities(
            provider_id="haven.fixture_vad",
            kind=VAD_KIND,
            capabilities=("streaming",),
        ),
    )
    registry.register(
        ScriptedAsrProvider(script=("haven", "turn off the bedroom lights")),
        capabilities=ProviderCapabilities(
            provider_id="haven.fixture_speech_to_text",
            kind=SPEECH_TO_TEXT_KIND,
            capabilities=("streaming", "partials"),
        ),
    )
    registry.register(
        ScriptedSynthesizer(),
        capabilities=ProviderCapabilities(
            provider_id="haven.fixture_text_to_speech",
            kind=TEXT_TO_SPEECH_KIND,
            capabilities=("interruptible",),
        ),
    )
    return registry


__all__ = [
    "INTELLIGENCE_KIND",
    "SPEECH_TO_TEXT_KIND",
    "TEXT_TO_SPEECH_KIND",
    "VAD_KIND",
    "WAKE_WORD_KIND",
    "build_default_registry",
]
