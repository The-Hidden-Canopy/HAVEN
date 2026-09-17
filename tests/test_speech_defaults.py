from haven.providers import build_default_registry
from haven.providers.defaults import (
    SPEECH_TO_TEXT_KIND,
    TEXT_TO_SPEECH_KIND,
    VAD_KIND,
    WAKE_WORD_KIND,
)
from haven.speech import (
    EnergyVad,
    ScriptedAsrProvider,
    ScriptedSynthesizer,
    ScriptedVad,
    ScriptedWakeDetector,
)
from haven.speech.vad import EnergyVad as VadPackageExport


def test_default_registry_exposes_each_speech_provider_by_kind():
    registry = build_default_registry()

    wake_ids = registry.find(kind=WAKE_WORD_KIND, requires=("preroll",))
    assert wake_ids == ("haven.fixture_wake_word",)
    assert isinstance(registry.get(wake_ids[0]), ScriptedWakeDetector)

    vad_ids = registry.find(kind=VAD_KIND, requires=("streaming",))
    assert vad_ids == ("haven.fixture_vad",)
    assert isinstance(registry.get(vad_ids[0]), EnergyVad)

    asr_ids = registry.find(kind=SPEECH_TO_TEXT_KIND, requires=("streaming", "partials"))
    assert asr_ids == ("haven.fixture_speech_to_text",)
    assert isinstance(registry.get(asr_ids[0]), ScriptedAsrProvider)

    tts_ids = registry.find(kind=TEXT_TO_SPEECH_KIND, requires=("interruptible",))
    assert tts_ids == ("haven.fixture_text_to_speech",)
    assert isinstance(registry.get(tts_ids[0]), ScriptedSynthesizer)


def test_speech_fixture_providers_are_deterministic():
    wake = ScriptedWakeDetector(triggers_at_frame=2)

    first = wake.process(bytes(800) * 2)
    second = wake.process(bytes(800) * 2)
    assert len(first) == 1
    assert second == []

    vad = ScriptedVad(script=((1, 2),))
    vad_events = []
    for _ in range(5):
        vad_events.extend(vad.process(bytes(800)))
    assert [type(event).__name__ for event in vad_events] == [
        "SpeechStarted",
        "SpeechContinued",
        "SpeechEnded",
    ]


def test_vad_package_reexports_energy_vad():
    assert VadPackageExport is EnergyVad
