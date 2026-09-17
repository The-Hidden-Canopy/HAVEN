"""The speech event vocabulary: what was heard and what was produced.

These are speech-layer events, not domain events: `at_ms` is an integer
count of milliseconds relative to the start of the stream or session, not
an aware-UTC timestamp. They describe observations and proposals. Nothing
in this module is authority: a transcript is a proposal that must clear
the household's approval boundary, and a speaker claim is evidence, never
identity truth.
"""

from __future__ import annotations

from dataclasses import dataclass

SAMPLE_RATE_HZ = 16000
"""Pinned wire rate: every provider in the speech layer exchanges 16 kHz audio."""

BYTES_PER_SAMPLE = 2
"""Pinned wire format: signed 16-bit little-endian."""

FRAME_MS = 25
"""Pinned analysis frame: 25 ms."""

SAMPLES_PER_FRAME = SAMPLE_RATE_HZ * FRAME_MS // 1000
"""400 samples per frame at the pinned rate."""

FRAME_BYTES = SAMPLES_PER_FRAME * BYTES_PER_SAMPLE
"""800 bytes per frame at the pinned rate."""


def _require_ms(value: int, *, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer number of milliseconds")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum} ms")
    return value


def _clamp_confidence(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0.0 and 1.0")
    return min(1.0, max(0.0, float(value)))


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class WakeEvent:
    """A wake phrase was detected in the audio stream."""

    confidence: float
    at_ms: int
    phrase: str = "haven"

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence, name="wake confidence"))
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="wake at_ms"))
        object.__setattr__(self, "phrase", _require_text(self.phrase, name="wake phrase"))


@dataclass(frozen=True)
class SpeechStarted:
    """The VAD declares that speech has begun at `at_ms`."""

    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="speech started at_ms"))


@dataclass(frozen=True)
class SpeechContinued:
    """The VAD observes that an ongoing utterance is still speech."""

    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="speech continued at_ms"))


@dataclass(frozen=True)
class SpeechEnded:
    """The VAD declares the utterance over, `duration_ms` after it began."""

    at_ms: int
    duration_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="speech ended at_ms"))
        object.__setattr__(self, "duration_ms", _require_ms(self.duration_ms, name="speech duration_ms", minimum=1))


@dataclass(frozen=True)
class TranscriptPartial:
    """An interim recognition hypothesis, for presentation only.

    A partial is never executable: it exists so the UI can show the words
    forming, and it carries no authority in either direction.
    """

    text: str
    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_text(self.text, name="partial text"))
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="partial at_ms"))


@dataclass(frozen=True)
class TranscriptFinal:
    """A committed recognition result over the interval [start_ms, end_ms].

    Only a final transcript may be consumed downstream: it is the only
    transcript shape a `SpeechSession` will hand out as haven input. An
    optional `speaker_claim` rides along as evidence, never as identity.
    """

    text: str
    start_ms: int
    end_ms: int
    confidence: float
    at_ms: int
    speaker_claim: SpeakerClaim | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_text(self.text, name="final text"))
        object.__setattr__(self, "start_ms", _require_ms(self.start_ms, name="final start_ms"))
        object.__setattr__(self, "end_ms", _require_ms(self.end_ms, name="final end_ms"))
        if self.end_ms < self.start_ms:
            raise ValueError("final end_ms must not precede start_ms")
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence, name="final confidence"))
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="final at_ms"))
        if self.speaker_claim is not None and not isinstance(self.speaker_claim, SpeakerClaim):
            raise ValueError("speaker_claim must be a SpeakerClaim")


@dataclass(frozen=True)
class SpeakerClaim:
    """A claim about who is speaking, never identity truth.

    This enters the world as evidence subject to the household's confidence
    bar: voice match is not permission. Downstream it may corroborate an
    actor hypothesis the way any other evidence does, but it never promotes
    anyone to authority on its own.
    """

    candidate_person_id: str
    confidence: float
    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_person_id",
            _require_text(self.candidate_person_id, name="candidate_person_id"),
        )
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence, name="speaker claim confidence"))
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="speaker claim at_ms"))


@dataclass(frozen=True)
class SpeechResponseStarted:
    """TTS playback began; downstream wake/VAD gating should suppress the echo."""

    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="response started at_ms"))


@dataclass(frozen=True)
class SpeechResponseStopped:
    """TTS playback ended; the echo-suppression gate may lift."""

    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_ms", _require_ms(self.at_ms, name="response stopped at_ms"))


__all__ = [
    "FRAME_BYTES",
    "FRAME_MS",
    "SAMPLES_PER_FRAME",
    "SAMPLE_RATE_HZ",
    "BYTES_PER_SAMPLE",
    "SpeechContinued",
    "SpeechEnded",
    "SpeechResponseStarted",
    "SpeechResponseStopped",
    "SpeechStarted",
    "SpeakerClaim",
    "TranscriptFinal",
    "TranscriptPartial",
    "WakeEvent",
]
