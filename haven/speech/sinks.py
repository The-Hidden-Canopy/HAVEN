"""Playback sinks: where synthesized PCM goes when HAVEN speaks.

A sink consumes what a `SpeechSynthesizer` yields. The vertical slice's
real artifact is `WavFileSink` -- a run of the service writes the actual
response audio to a 16 kHz mono 16-bit WAV file a resident can play back
or inspect -- while `NullSink` discards, for headless runs where TTS is
wired but unheard.
"""

from __future__ import annotations

import os
import wave
from collections.abc import Iterable
from typing import Protocol

from .events import SAMPLE_RATE_HZ


class PlaybackSink(Protocol):
    """A destination for synthesized PCM chunks.

    `play` consumes an iterable of PCM chunks (the first chunk is the
    prompt) and returns once the stream ends or is truncated by `stop`.
    Interrupting playback is a hard requirement of the speech layer:
    `stop` must take effect immediately, within one chunk, and must not
    require any model or LLM call.
    """

    def play(self, chunks: Iterable[bytes]) -> None:
        """Consume PCM chunks until exhausted or stopped."""
        ...

    def stop(self) -> None:
        """Truncate the current playback immediately."""
        ...


class WavFileSink:
    """Writes played PCM chunks to a 16 kHz mono 16-bit WAV file.

    The file is created on construction so a failed run still leaves an
    inspectable artifact header; `stop` finalizes and closes it. Chunks
    arrive already decoded (the synthesizer owns the wire envelope); this
    sink only serializes pinned-contract PCM.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = os.fspath(path)
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._writer: wave.Wave_write | None = wave.open(self._path, "wb")
        self._writer.setnchannels(1)
        self._writer.setsampwidth(2)
        self._writer.setframerate(SAMPLE_RATE_HZ)
        self._played = 0

    @property
    def path(self) -> str:
        return self._path

    @property
    def frames_played(self) -> int:
        """PCM frames (samples) written so far."""
        return self._played

    def play(self, chunks: Iterable[bytes]) -> None:
        if self._writer is None:
            raise ValueError("WavFileSink is stopped")
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray)):
                raise ValueError("playback chunks must be bytes")
            if not chunk:
                continue
            self._writer.writeframes(bytes(chunk))
            self._played += len(chunk) // 2

    def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None


class NullSink:
    """Discards every chunk; `stop` is a no-op flag.

    For runs where TTS is wired (so `say` exercises the full path) but no
    audible output is wanted. Records how many bytes were consumed so
    tests can assert playback actually happened.
    """

    def __init__(self) -> None:
        self._stop_requested = False
        self.bytes_played = 0

    def play(self, chunks: Iterable[bytes]) -> None:
        self._stop_requested = False
        for chunk in chunks:
            if self._stop_requested:
                return
            self.bytes_played += len(chunk)

    def stop(self) -> None:
        self._stop_requested = True


__all__ = ["NullSink", "PlaybackSink", "WavFileSink"]
