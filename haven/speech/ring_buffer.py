"""A fixed-duration pre-roll buffer over raw PCM bytes.

By the time a wake detector fires, part of the utterance has already
passed the mic: the resident is saying "Haven, turn off..." while the
detector is still confirming "Haven". `PrerollRingBuffer` keeps the most
recent `capacity_ms` of audio so capture can prepend it, yielding the
whole request instead of a truncated tail. It is bytes in, bytes out --
no decoding, no allocation beyond one bounded bytearray.
"""

from __future__ import annotations

from .events import FRAME_BYTES, FRAME_MS


class PrerollRingBuffer:
    """Retains the newest `capacity_ms` of PCM, oldest-to-newest on read."""

    def __init__(self, capacity_ms: int = 1000) -> None:
        if isinstance(capacity_ms, bool) or not isinstance(capacity_ms, int):
            raise ValueError("capacity_ms must be an integer number of milliseconds")
        if capacity_ms < FRAME_MS:
            raise ValueError(f"capacity_ms must be at least one frame ({FRAME_MS} ms)")
        self._capacity_ms = capacity_ms
        self._capacity_bytes = capacity_ms * FRAME_BYTES // FRAME_MS
        self._buffer = bytearray()

    @property
    def capacity_ms(self) -> int:
        return self._capacity_ms

    @property
    def size_bytes(self) -> int:
        return len(self._buffer)

    def write(self, pcm: bytes) -> None:
        """Append PCM, evicting the oldest bytes once capacity is exceeded."""
        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        if not pcm:
            return
        self._buffer += pcm
        overflow = len(self._buffer) - self._capacity_bytes
        if overflow > 0:
            del self._buffer[:overflow]

    def read_preroll(self) -> bytes:
        """Return the retained audio, oldest to newest, at most one capacity long."""
        return bytes(self._buffer)

    def clear(self) -> None:
        """Drop everything, e.g. once the pre-roll has been prepended to capture."""
        self._buffer.clear()


__all__ = ["PrerollRingBuffer"]
