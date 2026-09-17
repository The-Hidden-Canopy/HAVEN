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
from .session import SpeechSession, SpeechSessionState
from .vad import EnergyVad

__all__ = [
    "BYTES_PER_SAMPLE",
    "FRAME_BYTES",
    "FRAME_MS",
    "SAMPLES_PER_FRAME",
    "SAMPLE_RATE_HZ",
    "EnergyVad",
    "PrerollRingBuffer",
    "ScriptedAsrProvider",
    "ScriptedSynthesizer",
    "ScriptedVad",
    "ScriptedWakeDetector",
    "SpeechContinued",
    "SpeechEnded",
    "SpeechRecognizer",
    "SpeechResponseStarted",
    "SpeechResponseStopped",
    "SpeechSession",
    "SpeechSessionState",
    "SpeechStarted",
    "SpeechSynthesizer",
    "SpeakerClaim",
    "TranscriptFinal",
    "TranscriptPartial",
    "Vad",
    "WakeDetector",
    "WakeEvent",
]
