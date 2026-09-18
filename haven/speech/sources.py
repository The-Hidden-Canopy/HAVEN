"""Audio sources: where the PCM pumped by `SpeechService` comes from.

The pinned wire contract (16-bit signed little-endian mono, 16 kHz; one
25 ms frame is 400 samples or 800 bytes) is honored here at the boundary:
a source yields exactly `FRAME_BYTES` per `read_frame()` call and signals
end of stream with `b""`. The vertical slice is driven by real recorded
audio -- a WAV file recorded on any device -- so no microphone is
required to run the whole pipeline; `SilenceSource` stands in for tests.
"""

from __future__ import annotations

import os
import wave
from typing import Protocol

from .events import FRAME_BYTES, SAMPLE_RATE_HZ


class AudioSource(Protocol):
    """A streaming origin of pinned-contract PCM frames.

    `start` opens the underlying resource, `read_frame` yields exactly
    `FRAME_BYTES` of PCM (or `b""` once the stream is exhausted), `stop`
    releases the resource. Sources are pull-based and synchronous: the
    service's pump thread is what makes them a stream.
    """

    def start(self) -> None:
        """Open the underlying resource; frames become readable."""
        ...

    def read_frame(self) -> bytes:
        """Return exactly one frame of PCM, or `b""` at end of stream."""
        ...

    def stop(self) -> None:
        """Release the underlying resource."""
        ...


class WavFileSource:
    """Reads pinned-contract PCM frames from a WAV file.

    The file is validated on construction: 16 kHz, 16-bit, mono. Anything
    else is a clear `ValueError` -- the wire contract is pinned, so a
    mismatched file is rejected rather than resampled silently. Frames are
    carved from the sample stream; a trailing partial frame (a recording
    cut mid-frame) is surfaced by the final `read_frame()` as a short
    chunk so no audio is silently dropped.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = os.fspath(path)
        with wave.open(self._path, "rb") as reader:
            self._channels = reader.getnchannels()
            self._width = reader.getsampwidth()
            self._rate = reader.getframerate()
            self._frames = reader.getnframes()
        if self._rate != SAMPLE_RATE_HZ:
            raise ValueError(
                f"{self._path}: sample rate must be {SAMPLE_RATE_HZ} Hz, got {self._rate}"
            )
        if self._width != 2:
            raise ValueError(f"{self._path}: sample width must be 2 bytes (16-bit), got {self._width}")
        if self._channels != 1:
            raise ValueError(f"{self._path}: channel count must be 1 (mono), got {self._channels}")
        self._reader: wave.Wave_read | None = None

    @property
    def path(self) -> str:
        return self._path

    def start(self) -> None:
        if self._reader is None:
            self._reader = wave.open(self._path, "rb")

    def read_frame(self) -> bytes:
        if self._reader is None:
            raise ValueError("WavFileSource must be started before reading")
        data = self._reader.readframes(FRAME_BYTES // 2)
        return data if data else b""

    def stop(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


class SilenceSource:
    """Emits `frames` frames of digital silence, then end of stream.

    The fixture that lets a pipeline run without any recording: every
    frame is `FRAME_BYTES` of zeroed PCM.
    """

    def __init__(self, frames: int) -> None:
        if isinstance(frames, bool) or not isinstance(frames, int) or frames < 0:
            raise ValueError("frames must be a non-negative integer")
        self._remaining = frames
        self._started = False

    def start(self) -> None:
        self._started = True

    def read_frame(self) -> bytes:
        if not self._started:
            raise ValueError("SilenceSource must be started before reading")
        if self._remaining <= 0:
            return b""
        self._remaining -= 1
        return b"\x00" * FRAME_BYTES

    def stop(self) -> None:
        self._remaining = 0
        self._started = False


__all__ = ["AudioSource", "SilenceSource", "WavFileSource"]
