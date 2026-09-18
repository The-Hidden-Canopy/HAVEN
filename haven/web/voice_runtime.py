"""Composes a real, always-on `SpeechService` from assigned models + hardware.

This is the seam item 10 (the voice hardware loop) closes: `SpeechService`
and the wake/VAD/ASR/TTS protocols were already hardware-agnostic, and
`haven.speech.native_audio` now provides a real microphone/speaker. This
module is what joins them to a running `HavenApplication` -- resolving
role-assigned models through the same manager the rest of HAVEN already
uses, adapting their loaded handles through `resolve_speech_shims`, and
handing back a `SpeechService` ready to `start()`.

Degradation is structural, not a try/except sprinkled at the call site:
`build_speech_service` returns `None` (never raises) whenever a real voice
loop cannot be assembled -- no native audio on this platform, no
microphone/speaker device, or a wake model with no paired ASR model to
finish what it wakes (see the module docstring on `_resolve_wake_and_asr`).
A household with no speech models loaded keeps working exactly as before,
through the browser's simulated `VoiceSession` -- this module never removes
that fallback; it only adds a real one alongside it when one is possible.
"""

from __future__ import annotations

from typing import Callable

from haven.models.manager import (
    ROLE_ASR,
    ROLE_REQUIREMENTS,
    ROLE_TTS,
    ROLE_WAKE_WORD,
    ModelManager,
    ModelNotFoundError,
)
from haven.speech.events import TranscriptFinal
from haven.speech.native_audio import WinMMError, WinMMMicrophoneSource, WinMMSpeakerSink
from haven.speech.protocols import SpeechSynthesizer
from haven.speech.service import SpeechService
from haven.speech.shims import resolve_speech_shims


def _resolved_handle(model_manager: ModelManager, role: str):
    """The handle for the role-assigned model if loaded, else the best LOADED
    model satisfying that role's kind + capability requirement, else None.

    Mirrors `ModelIntelligenceProvider._role_handle`'s resolution order
    (`haven.models.bridge`), independently of it: wake-word has no
    intelligence-provider equivalent, and this must work the same way
    whether or not intelligence is enabled for this household.
    """

    model_id = model_manager.assigned(role)
    if model_id is not None:
        handle = model_manager.loaded_handle(model_id)
        if handle is not None:
            return handle
    kind, required = ROLE_REQUIREMENTS[role]
    try:
        descriptor = model_manager.resolve(kind, requires=required)
    except ModelNotFoundError:
        return None
    return model_manager.loaded_handle(descriptor.id)


def resolve_tts_synthesizer(model_manager: ModelManager) -> SpeechSynthesizer | None:
    """The role-assigned TTS model as a `SpeechSynthesizer`, or None.

    Deliberately independent of `resolve_voice_shims`/wake+ASR: a household
    can have real spoken *output* (this) without HAVEN owning the
    microphone at all -- wake-word and transcription may come from
    somewhere else entirely (another always-on input pipeline this
    household already runs). Text HAVEN says (`_say("haven", ...)`) is
    worth actually speaking whenever a TTS model is assigned, whether or
    not a real always-on capture pump is also running.
    """

    handle = _resolved_handle(model_manager, ROLE_TTS)
    if handle is None:
        return None
    return resolve_speech_shims(tts_handle=handle).tts


def resolve_voice_shims(model_manager: ModelManager):
    """Resolve wake/asr/tts handles into `SpeechShims`, or None if unusable.

    Pure model-manager logic, no hardware -- separated from
    `build_speech_service` so the resolution rules (importantly: a wake
    model is only wired paired with an ASR model) are testable without a
    real microphone/speaker. `SpeechService` raises `NoSpeechModelError`
    (killing its own pump thread) if an utterance finishes with no
    recognizer to transcribe it, so a wake-only partial deployment would
    crash on the very first detection -- wiring neither is the honest
    degradation, not wiring a detector that cannot finish what it starts.
    With no wake detector, nothing in HAVEN today ever calls
    `begin_utterance()` either (there is no push-to-talk trigger wired to
    this service), so None is returned rather than a service that could
    never produce anything. TTS has no such hazard (`say` is a documented
    no-op with nothing wired) and is included whenever available even
    without a wake+ASR pair.
    """

    asr_handle = _resolved_handle(model_manager, ROLE_ASR)
    wake_handle = _resolved_handle(model_manager, ROLE_WAKE_WORD) if asr_handle is not None else None
    if wake_handle is None:
        return None
    tts_handle = _resolved_handle(model_manager, ROLE_TTS)
    return resolve_speech_shims(wake_handle=wake_handle, asr_handle=asr_handle, tts_handle=tts_handle)


def build_speech_service(
    *,
    model_manager: ModelManager,
    on_utterance: Callable[[TranscriptFinal], None],
    on_speech_state: Callable[[str], None],
) -> SpeechService | None:
    """Build a real, always-on `SpeechService`, or None if one isn't possible.

    None whenever `resolve_voice_shims` finds no usable wake+ASR pair, or
    the native audio device can't be opened (no native audio on this
    platform, no microphone/speaker device). A household with no speech
    models loaded keeps working exactly as before, through the browser's
    simulated `VoiceSession` -- this never removes that fallback, only adds
    a real one alongside it when one is possible.
    """

    shims = resolve_voice_shims(model_manager)
    if shims is None:
        return None

    try:
        source = WinMMMicrophoneSource()
        sink = WinMMSpeakerSink()
    except WinMMError:
        return None

    return SpeechService(
        source=source,
        wake=shims.wake,
        recognizer=shims.asr,
        synthesizer=shims.tts,
        sink=sink,
        on_utterance=on_utterance,
        on_speech_state=on_speech_state,
    )


__all__ = ["build_speech_service", "resolve_tts_synthesizer", "resolve_voice_shims"]
