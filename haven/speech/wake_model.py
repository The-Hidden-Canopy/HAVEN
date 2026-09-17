"""The HAVEN-KWS wake-word model artifact contract.

The wake-word model is an artifact served by inference providers; HAVEN
owns the contract. `KwsModelManifest` pins everything a deployment needs to
run a keyword-spotting artifact honestly: who it is (`model_id`, `version`),
how it hears (`sample_rate_hz`, `frame_ms`, `window_ms`), and when it may
claim a detection (`threshold`, `debounce_ms`, `positive_phrase`).

`ThresholdedWakeDetector` turns a manifest plus a `scorer` callable into a
protocol-conforming `WakeDetector` in pure Python: a rolling window over
buffered PCM frames, scored per frame, firing one `WakeEvent` per threshold
crossing and re-arming only once the rolling score falls back below the
threshold.

The scorer is the injection point between the contract and the artifact:
`scorer(window_pcm) -> float` maps one window of 16-bit LE mono PCM to
P(positive). A real HAVEN-KWS artifact -- feature extraction in
`native/haven-voice`, inference served by the household's inference stack
-- arrives as a scorer adapter (e.g. a thin wrapper over the HTTP seam in
`haven.speech.providers`); tests arrive as scripted scorers. The detector
never cares which.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .events import BYTES_PER_SAMPLE, WakeEvent


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_int(value: int, *, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _require_probability(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0.0 and 1.0")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")
    return float(value)


@dataclass(frozen=True)
class KwsModelManifest:
    """The contract a HAVEN-KWS artifact is served under.

    `threshold` is the P(positive) at or above which the rolling window
    counts as an activation; `debounce_ms` is the minimum time between two
    fired events; `positive_phrase` is the phrase a detection claims.
    """

    model_id: str
    version: str
    sample_rate_hz: int = 16000
    frame_ms: int = 25
    window_ms: int = 1000
    threshold: float = 0.5
    debounce_ms: int = 750
    positive_phrase: str = "haven"

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _require_text(self.model_id, name="model_id"))
        object.__setattr__(self, "version", _require_text(self.version, name="version"))
        object.__setattr__(
            self,
            "sample_rate_hz",
            _require_int(self.sample_rate_hz, name="sample_rate_hz", minimum=1),
        )
        object.__setattr__(self, "frame_ms", _require_int(self.frame_ms, name="frame_ms", minimum=1))
        object.__setattr__(
            self,
            "window_ms",
            _require_int(self.window_ms, name="window_ms", minimum=self.frame_ms),
        )
        object.__setattr__(self, "threshold", _require_probability(self.threshold, name="threshold"))
        object.__setattr__(self, "debounce_ms", _require_int(self.debounce_ms, name="debounce_ms", minimum=0))
        object.__setattr__(self, "positive_phrase", _require_text(self.positive_phrase, name="positive_phrase"))

    @property
    def frame_bytes(self) -> int:
        """One analysis frame of PCM at the manifest's rate, in bytes."""
        return self.sample_rate_hz * self.frame_ms * BYTES_PER_SAMPLE // 1000

    @property
    def window_bytes(self) -> int:
        """The rolling scoring window of PCM at the manifest's rate, in bytes."""
        return self.sample_rate_hz * self.window_ms * BYTES_PER_SAMPLE // 1000


class ThresholdedWakeDetector:
    """Rolling-window wake detector driven by a manifest and a scorer.

    PCM arrives in any chunk size; whole `frame_ms` frames accumulate into
    a rolling window of `window_ms`. Each new frame is scored once; a score
    at or above the threshold fires exactly one `WakeEvent` (debounced by
    `debounce_ms`), and the detector re-arms only after the rolling score
    falls below the threshold. With no injected clock the stream's own
    frame timeline is the clock, so the detector is fully deterministic;
    inject `clock_ms` to govern debounce timing from the outside.
    """

    def __init__(
        self,
        manifest: KwsModelManifest,
        scorer: Callable[[bytes], float],
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(manifest, KwsModelManifest):
            raise ValueError("manifest must be a KwsModelManifest")
        if not callable(scorer):
            raise ValueError("scorer must be a callable mapping PCM window bytes to P(positive)")
        if clock_ms is not None and not callable(clock_ms):
            raise ValueError("clock_ms must be callable")
        self._manifest = manifest
        self._scorer = scorer
        self._clock_ms = clock_ms
        self._frame_bytes = manifest.frame_bytes
        self._window_bytes = manifest.window_bytes
        self._pending = bytearray()
        self._window = bytearray()
        self._frames_seen = 0
        self._armed = True
        self._last_fire_clock_ms = -manifest.debounce_ms

    def process(self, pcm: bytes) -> list[WakeEvent]:
        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        events: list[WakeEvent] = []
        self._pending += pcm
        while len(self._pending) >= self._frame_bytes:
            frame = bytes(self._pending[: self._frame_bytes])
            del self._pending[: self._frame_bytes]
            self._frames_seen += 1
            self._window += frame
            overflow = len(self._window) - self._window_bytes
            if overflow > 0:
                del self._window[:overflow]
            score = self._scorer(bytes(self._window))
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError("scorer must return a number between 0.0 and 1.0")
            score = min(1.0, max(0.0, float(score)))
            if score >= self._manifest.threshold:
                if self._armed and self._now_ms() - self._last_fire_clock_ms >= self._manifest.debounce_ms:
                    events.append(
                        WakeEvent(
                            confidence=score,
                            at_ms=self._frames_seen * self._manifest.frame_ms,
                            phrase=self._manifest.positive_phrase,
                        )
                    )
                    self._armed = False
                    self._last_fire_clock_ms = self._now_ms()
            else:
                self._armed = True
        return events

    def _now_ms(self) -> int:
        if self._clock_ms is not None:
            return self._clock_ms()
        return self._frames_seen * self._manifest.frame_ms


__all__ = ["KwsModelManifest", "ThresholdedWakeDetector"]
