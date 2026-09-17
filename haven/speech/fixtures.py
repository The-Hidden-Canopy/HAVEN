"""Scripted, deterministic providers for tests and the demo slice.

Every provider here implements one of the protocols in
`haven.speech.protocols` with behavior driven entirely by constructor
arguments -- no hardware, no randomness, no model. They exist so the
session state machine, the demo flow, and the capability registry can be
exercised end to end before any real DSP or inference exists.
"""

from __future__ import annotations

from collections.abc import Iterable

from .events import (
    FRAME_BYTES,
    FRAME_MS,
    SpeechContinued,
    SpeechEnded,
    SpeechStarted,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _frames_in(pcm: bytes) -> int:
    # A provider counts whole 25 ms frames; a short tail counts as one.
    return max(1, len(pcm) // FRAME_BYTES)


class ScriptedWakeDetector:
    """Fires a `WakeEvent` once the stream reaches `triggers_at_frame` frames."""

    def __init__(self, *, triggers_at_frame: int = 1, phrase: str = "haven", confidence: float = 0.9) -> None:
        if isinstance(triggers_at_frame, bool) or not isinstance(triggers_at_frame, int) or triggers_at_frame < 1:
            raise ValueError("triggers_at_frame must be an integer of at least 1")
        self._triggers_at_frame = triggers_at_frame
        self._phrase = _require_text(phrase, name="phrase")
        self._confidence = confidence
        self._frames_seen = 0
        self._fired = False

    def process(self, pcm: bytes) -> list[WakeEvent]:
        if self._fired:
            return []
        events: list[WakeEvent] = []
        for _ in range(_frames_in(pcm)):
            frame_at_ms = self._frames_seen * FRAME_MS
            self._frames_seen += 1
            if not self._fired and self._frames_seen >= self._triggers_at_frame:
                events.append(WakeEvent(confidence=self._confidence, at_ms=frame_at_ms, phrase=self._phrase))
                self._fired = True
        return events


class ScriptedVad:
    """Segments the stream according to declared speech spans.

    `script` is a sequence of `(start_frame, end_frame)` pairs, both
    inclusive, numbering frames of 25 ms. Every frame inside a span that
    is not the span's first yields a `SpeechContinued`.
    """

    def __init__(self, script: Iterable[tuple[int, int]] = ()) -> None:
        spans = tuple((int(start), int(end)) for start, end in script)
        for start, end in spans:
            if start < 0 or end < start:
                raise ValueError("speech spans must be non-negative (start_frame, end_frame) pairs")
        self._spans = spans
        self._starts = {start: end for start, end in spans}
        self._frame_after_end = {end + 1: (start, end) for start, end in spans}
        self._frames_seen = 0

    def process(self, pcm: bytes) -> list[SpeechStarted | SpeechContinued | SpeechEnded]:
        events: list[SpeechStarted | SpeechContinued | SpeechEnded] = []
        for _ in range(_frames_in(pcm)):
            frame = self._frames_seen
            self._frames_seen += 1
            at_ms = frame * FRAME_MS
            if frame in self._starts:
                events.append(SpeechStarted(at_ms))
                continue
            if frame in self._frame_after_end:
                start, end = self._frame_after_end[frame]
                events.append(SpeechEnded(at_ms, duration_ms=(end - start + 1) * FRAME_MS))
            if any(start <= frame <= end for start, end in self._spans):
                events.append(SpeechContinued(at_ms))
        return events


class ScriptedAsrProvider:
    """Emits the scripted partials while fed audio, then one final.

    Every element of `script` except the last becomes a `TranscriptPartial`
    returned by successive `accept` calls; the last becomes the single
    `TranscriptFinal` returned by `finalize()`. Deterministic and
    single-shot, like a scripted endpointed utterance.
    """

    def __init__(self, script: Iterable[str], *, confidence: float = 0.95) -> None:
        self._partials = [_require_text(item, name="script item") for item in script]
        if not self._partials:
            raise ValueError("script must contain at least the final transcript")
        self._final_text = self._partials.pop()
        self._confidence = confidence
        self._accepts = 0
        self._finalized = False

    def accept(self, pcm: bytes) -> list[TranscriptPartial | TranscriptFinal]:
        if not self._partials:
            return []
        self._accepts += 1
        text = self._partials.pop(0)
        return [TranscriptPartial(text=text, at_ms=self._accepts * 100)]

    def finalize(self) -> list[TranscriptFinal]:
        if self._finalized:
            return []
        self._finalized = True
        at_ms = (self._accepts + 1) * 100
        return [
            TranscriptFinal(
                text=self._final_text,
                start_ms=0,
                end_ms=at_ms,
                confidence=self._confidence,
                at_ms=at_ms,
            )
        ]


class ScriptedSynthesizer:
    """Yields a fixed number of PCM chunks per utterance, truncatable.

    `speak` yields a priming chunk first so playback can begin before the
    scripted audio, then `chunk_count` deterministic chunks. `stop`
    truncates the stream immediately -- no model, no round trip -- which is
    the property barge-in depends on.
    """

    def __init__(self, *, chunk_count: int = 8, chunk_bytes: int = FRAME_BYTES) -> None:
        if isinstance(chunk_count, bool) or not isinstance(chunk_count, int) or chunk_count < 1:
            raise ValueError("chunk_count must be an integer of at least 1")
        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes < 1:
            raise ValueError("chunk_bytes must be an integer of at least 1")
        self._chunk_count = chunk_count
        self._chunk_bytes = chunk_bytes
        self._stop_requested = False

    def speak(self, text: str) -> Iterable[bytes]:
        _require_text(text, name="text")
        self._stop_requested = False

        def stream() -> Iterable[bytes]:
            yield b""  # priming chunk: playback may start before the audio
            for index in range(self._chunk_count):
                if self._stop_requested:
                    return
                yield bytes((index & 0xFF,)) * self._chunk_bytes

        return stream()

    def stop(self) -> None:
        self._stop_requested = True


__all__ = ["ScriptedAsrProvider", "ScriptedSynthesizer", "ScriptedVad", "ScriptedWakeDetector"]
