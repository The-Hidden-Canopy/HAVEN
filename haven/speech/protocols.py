"""The public speech provider protocols.

These `typing.Protocol`s are the entire surface a speech provider plugs
into: an implementer needs nothing from HAVEN internals beyond the event
dataclasses and the pinned PCM wire contract (16-bit signed little-endian
mono, 16 kHz; one 25 ms frame is 400 samples or 800 bytes). The heavy
implementations -- DSP, model inference, training -- live outside core
(future `native/haven-voice`); anything conforming to these protocols can
serve behind the same capability registry.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from .events import (
    SpeechContinued,
    SpeechEnded,
    SpeechStarted,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)


class WakeDetector(Protocol):
    """Detects a wake phrase in a stream of raw PCM.

    `process` consumes one frame (or any chunk) of 16 kHz 16-bit LE mono
    PCM and returns zero or more detections. Implementations must be
    streaming: audio arrives as it is captured and detections refer back to
    where the phrase actually occurred, because the utterance has already
    begun by the time the detector fires.
    """

    def process(self, pcm: bytes) -> list[WakeEvent]:
        """Consume one chunk of PCM and return any detections within it."""
        ...


class Vad(Protocol):
    """Answers "is somebody speaking", never "what did they say".

    A VAD segments the stream into speech and non-speech. Transcribing,
    identifying a speaker, or interpreting intent is a different provider's
    job; keeping the boundary here is what lets a privacy-preserving home
    run the VAD without ever producing text.
    """

    def process(self, pcm: bytes) -> list[SpeechStarted | SpeechContinued | SpeechEnded]:
        """Consume one chunk of PCM and return any segmentation events."""
        ...


class SpeechRecognizer(Protocol):
    """Streaming speech-to-text over the captured utterance.

    `accept` consumes audio as it streams and may return interim
    `TranscriptPartial` hypotheses; `finalize` performs endpointing and
    commits whatever remains as `TranscriptFinal` results. Downstream code
    consumes only finals: partials are for presentation.
    """

    def accept(self, pcm: bytes) -> list[TranscriptPartial | TranscriptFinal]:
        """Consume one chunk of PCM and return any interim or final results."""
        ...

    def finalize(self) -> list[TranscriptFinal]:
        """Endpoint the stream and commit all remaining final transcripts."""
        ...


class SpeechSynthesizer(Protocol):
    """Text-to-speech playback that can be interrupted at any moment.

    `speak` returns an iterable of PCM chunks; the first chunk is a prompt
    so playback can begin before synthesis completes. Interrupting playback
    is a hard requirement of the speech layer: `stop` must truncate the
    stream immediately and must not require any model or LLM call, so a
    barge-in ("haven, stop") can silence the speaker within one chunk.
    """

    def speak(self, text: str) -> Iterable[bytes]:
        """Yield PCM chunks for `text`; the first chunk is a prompt."""
        ...

    def stop(self) -> None:
        """Truncate the current utterance without any model/LLM call."""
        ...


__all__ = ["SpeechRecognizer", "SpeechSynthesizer", "Vad", "WakeDetector"]
