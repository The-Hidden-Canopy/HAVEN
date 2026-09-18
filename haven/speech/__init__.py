"""The public HAVEN speech contract: events, protocols, and building blocks.

This package is step one of the speech subsystem. It defines the wire
contract (16-bit signed little-endian mono, 16 kHz; one 25 ms frame is
400 samples or 800 bytes), the event vocabulary, the provider protocols a
speech implementation plugs into, and pure-Python building blocks plus
scripted fixtures. Everything here is stdlib-only; the heavy pieces -- DSP,
model inference, training -- will live in `native/haven-voice` behind the
same protocols.
"""

from .events import (
    BYTES_PER_SAMPLE,
    FRAME_BYTES,
    FRAME_MS,
    SAMPLES_PER_FRAME,
    SAMPLE_RATE_HZ,
    SpeechContinued,
    SpeechEnded,
    SpeechResponseStarted,
    SpeechResponseStopped,
    SpeechStarted,
    SpeakerClaim,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)
from .fixtures import (
    ScriptedAsrProvider,
    ScriptedSynthesizer,
    ScriptedVad,
    ScriptedWakeDetector,
)
from .protocols import SpeechRecognizer, SpeechSynthesizer, Vad, WakeDetector
from .ring_buffer import PrerollRingBuffer
from .service import NoSpeechModelError, SpeechService
from .session import SpeechSession, SpeechSessionState
from .shims import (
    AsrModelRecognizer,
    SpeechModelCapabilityError,
    SpeechShims,
    TtsModelSynthesizer,
    WakeModelDetector,
    resolve_speech_shims,
)
from .sinks import NullSink, PlaybackSink, WavFileSink
from .sources import AudioSource, SilenceSource, WavFileSource
from .vad import EnergyVad

__all__ = [
    "AsrModelRecognizer",
    "AudioSource",
    "BYTES_PER_SAMPLE",
    "FRAME_BYTES",
    "FRAME_MS",
    "SAMPLES_PER_FRAME",
    "SAMPLE_RATE_HZ",
    "EnergyVad",
    "NoSpeechModelError",
    "NullSink",
    "PlaybackSink",
    "PrerollRingBuffer",
    "ScriptedAsrProvider",
    "ScriptedSynthesizer",
    "ScriptedVad",
    "ScriptedWakeDetector",
    "SilenceSource",
    "SpeechContinued",
    "SpeechEnded",
    "SpeechModelCapabilityError",
    "SpeechRecognizer",
    "SpeechResponseStarted",
    "SpeechResponseStopped",
    "SpeechService",
    "SpeechSession",
    "SpeechSessionState",
    "SpeechShims",
    "SpeechStarted",
    "SpeechSynthesizer",
    "SpeakerClaim",
    "TranscriptFinal",
    "TranscriptPartial",
    "TtsModelSynthesizer",
    "Vad",
    "WakeDetector",
    "WakeEvent",
    "WakeModelDetector",
    "WavFileSink",
    "WavFileSource",
    "resolve_speech_shims",
]
