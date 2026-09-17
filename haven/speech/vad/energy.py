"""Energy-based reference VAD over the pinned 16 kHz 16-bit LE PCM stream."""

from __future__ import annotations

import math
import struct

from ..events import (
    FRAME_MS,
    SAMPLES_PER_FRAME,
    SAMPLE_RATE_HZ,
    SpeechContinued,
    SpeechEnded,
    SpeechStarted,
)

_FULL_SCALE = 32768.0
# A frame quieter than -90 dBFS is treated as digital silence, and the
# noise floor is never allowed to ride up into ordinary room tone.
_SILENCE_DB = -90.0
_MAX_FLOOR_DB = -20.0
_FLOOR_INIT_DB = -60.0
# How quickly the noise floor chases quiet frames when no speech is present.
_FLOOR_ADAPT = 0.1


class EnergyVad:
    """RMS energy per frame against an adapting floor, with hysteresis.

    A frame is speech when it rises `onset_db` above the noise floor.
    Already-running speech keeps going until it falls to `release_db`
    (a lower bar, so a breath or dip does not cut the utterance), and only
    then for another `hold_ms` of continuous quiet, so `SpeechEnded` lags
    the last voiced frame instead of flickering. New speech must persist
    `min_speech_ms` before `SpeechStarted` is emitted. The floor adapts
    toward the quiet frames it sees and freezes while speech is present,
    tracking a changing room without swallowing the utterance.
    """

    def __init__(
        self,
        *,
        frame_ms: int = FRAME_MS,
        onset_db: float = 12.0,
        release_db: float = 6.0,
        hold_ms: int = 300,
        min_speech_ms: int = 100,
    ) -> None:
        if isinstance(frame_ms, bool) or not isinstance(frame_ms, int) or frame_ms < 10:
            raise ValueError("frame_ms must be an integer of at least 10")
        if isinstance(onset_db, bool) or not isinstance(onset_db, (int, float)) or onset_db <= 0:
            raise ValueError("onset_db must be a positive number of dB")
        if isinstance(release_db, bool) or not isinstance(release_db, (int, float)) or release_db <= 0:
            raise ValueError("release_db must be a positive number of dB")
        if release_db > onset_db:
            raise ValueError("release_db must not exceed onset_db")
        if isinstance(hold_ms, bool) or not isinstance(hold_ms, int) or hold_ms < 0:
            raise ValueError("hold_ms must be a non-negative integer")
        if isinstance(min_speech_ms, bool) or not isinstance(min_speech_ms, int) or min_speech_ms < frame_ms:
            raise ValueError("min_speech_ms must be an integer of at least one frame")

        self._frame_ms = frame_ms
        self._samples_per_frame = SAMPLE_RATE_HZ * frame_ms // 1000
        self._frame_bytes = self._samples_per_frame * 2
        self._onset_db = float(onset_db)
        self._release_db = float(release_db)
        self._hold_frames = math.ceil(hold_ms / frame_ms)
        self._min_speech_frames = math.ceil(min_speech_ms / frame_ms)

        self._leftover = b""
        self._samples_seen = 0
        self._floor_db = _FLOOR_INIT_DB
        self._in_speech = False
        self._candidate_frames = 0
        self._speech_start_ms = 0
        self._quiet_frames = 0

    def process(self, pcm: bytes) -> list[SpeechStarted | SpeechContinued | SpeechEnded]:
        """Consume one chunk of PCM and return any segmentation events."""
        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        events: list[SpeechStarted | SpeechContinued | SpeechEnded] = []
        data = self._leftover + bytes(pcm)
        frame_count, remainder = divmod(len(data), self._frame_bytes)
        self._leftover = data[len(data) - remainder:] if remainder else b""
        for index in range(frame_count):
            frame = data[index * self._frame_bytes:(index + 1) * self._frame_bytes]
            at_ms = self._samples_seen * 1000 // SAMPLE_RATE_HZ
            self._samples_seen += self._samples_per_frame
            events.extend(self._classify(frame, at_ms))
        return events

    def _frame_db(self, frame: bytes) -> float:
        total = 0
        for (sample,) in struct.iter_unpack("<h", frame):
            total += sample * sample
        rms = math.sqrt(total / self._samples_per_frame)
        if rms < 1.0:
            return _SILENCE_DB
        return 20.0 * math.log10(rms / _FULL_SCALE)

    def _classify(self, frame: bytes, at_ms: int) -> list[SpeechStarted | SpeechContinued | SpeechEnded]:
        db = self._frame_db(frame)
        if self._in_speech:
            if db >= self._floor_db + self._release_db:
                self._quiet_frames = 0
                return [SpeechContinued(at_ms)]
            self._quiet_frames += 1
            if self._quiet_frames >= self._hold_frames:
                self._in_speech = False
                return [SpeechEnded(at_ms=at_ms, duration_ms=at_ms - self._speech_start_ms)]
            return []

        if db >= self._floor_db + self._onset_db:
            if self._candidate_frames == 0:
                self._speech_start_ms = at_ms
            self._candidate_frames += 1
            if self._candidate_frames >= self._min_speech_frames:
                self._in_speech = True
                self._quiet_frames = 0
                return [SpeechStarted(at_ms=self._speech_start_ms)]
            return []

        self._candidate_frames = 0
        self._floor_db = min(
            _MAX_FLOOR_DB,
            max(_SILENCE_DB, self._floor_db + _FLOOR_ADAPT * (db - self._floor_db)),
        )
        return []


__all__ = ["EnergyVad"]
