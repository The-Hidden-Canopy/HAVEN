"""Thin HTTP adapters from the speech protocols to a remote inference endpoint.

HAVEN contains no model and no inference runtime: the household's own
inference stack serves every artifact, and these adapters are the thin
remote seam. Each adapter satisfies one protocol from
`haven.speech.protocols` by POSTing the pinned PCM wire contract (16-bit
signed little-endian mono, 16 kHz) as base16 inside a small JSON envelope.

Per-frame HTTP would be absurd, so the wake adapter batches by design: it
accumulates pre-roll style and only queries the endpoint once per
`query_every_ms` of buffered audio. A production deployment puts the model
behind a persistent connection or in-process; this module is the thin
remote seam, not the production transport.

Endpoint or connection failures raise `InferenceUnavailableError`; callers
must treat that as evidence-unavailable, never as a transcript, a detection,
or audio.

Registration: these adapters are NOT in `haven/providers/defaults.py`.
The default registry stays zero-config fixtures; an inference adapter
requires an endpoint, so the deployer constructs it at deployment time and
registers it with the capability registry themselves.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..events import (
    BYTES_PER_SAMPLE,
    FRAME_BYTES,
    FRAME_MS,
    SAMPLE_RATE_HZ,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class InferenceUnavailableError(RuntimeError):
    """The inference endpoint could not be reached or answered unusably.

    This is evidence-unavailable, never a transcript or detection: a caller
    that sees it must fail closed the same way it would on stale evidence.
    """


@dataclass(frozen=True)
class InferenceEndpointConfig:
    """Where the household's inference stack lives and which model to ask for."""

    base_url: str
    model_id: str
    timeout_seconds: float = 5.0
    api_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _require_text(self.base_url, name="base_url"))
        object.__setattr__(self, "model_id", _require_text(self.model_id, name="model_id"))
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number of seconds")
        if self.api_key is not None:
            object.__setattr__(self, "api_key", _require_text(self.api_key, name="api_key"))


def _post_json(config: InferenceEndpointConfig, path: str, payload: dict[str, Any]) -> Any:
    """POST one JSON envelope and return the decoded JSON response.

    Any transport failure, non-2xx status, or undecodable body raises
    `InferenceUnavailableError` -- the endpoint is either there and
    well-formed or the evidence is unavailable.
    """

    url = config.base_url.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if config.api_key is not None:
        request.add_header("Authorization", f"Bearer {config.api_key}")
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InferenceUnavailableError(f"inference endpoint {url} unreachable: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InferenceUnavailableError(f"inference endpoint {url} returned unusable JSON: {exc}") from exc


def _require_mapping(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InferenceUnavailableError(f"{where} response must be a JSON object")
    return value


def _require_key(payload: dict[str, Any], key: str, *, where: str) -> Any:
    if key not in payload:
        raise InferenceUnavailableError(f"{where} response is missing {key!r}")
    return payload[key]


class InferenceWakeDetector:
    """Batched wake-word scoring against `{base_url}/wake/score`.

    Incoming PCM accumulates pre-roll style; every `query_every_ms` of
    buffered audio becomes ONE POST of `{"model_id", "pcm_base16"}` (the
    pinned 16-bit LE wire format as hex). The endpoint answers
    `{"score": float, "phrase": "haven"}`; a score at or above `threshold`
    is one activation, and `min_activations` consecutive activated windows
    fire exactly one `WakeEvent` (at the end of the window that completed
    the streak). After firing the detector is debounced: it stays silent
    until the score drops below threshold, then re-arms and demands a fresh
    streak. Batching is the point: per-frame HTTP would be absurd, and a
    production deployment swaps this seam for a persistent or in-process
    connection without touching the protocol.
    """

    def __init__(
        self,
        config: InferenceEndpointConfig,
        *,
        query_every_ms: int = 500,
        threshold: float = 0.5,
        min_activations: int = 2,
    ) -> None:
        if isinstance(query_every_ms, bool) or not isinstance(query_every_ms, int) or query_every_ms < FRAME_MS:
            raise ValueError(f"query_every_ms must be an integer of at least one frame ({FRAME_MS} ms)")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be a number between 0.0 and 1.0")
        if isinstance(min_activations, bool) or not isinstance(min_activations, int) or min_activations < 1:
            raise ValueError("min_activations must be an integer of at least 1")
        self._config = config
        self._query_bytes = query_every_ms * SAMPLE_RATE_HZ * BYTES_PER_SAMPLE // 1000
        self._threshold = float(threshold)
        self._min_activations = min_activations
        self._buffer = bytearray()
        self._consumed_bytes = 0
        self._streak = 0
        self._debounced = False

    def process(self, pcm: bytes) -> list[WakeEvent]:
        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        events: list[WakeEvent] = []
        self._buffer += pcm
        while len(self._buffer) >= self._query_bytes:
            window = bytes(self._buffer[: self._query_bytes])
            del self._buffer[: self._query_bytes]
            self._consumed_bytes += len(window)
            response = _require_mapping(
                _post_json(
                    self._config,
                    "/wake/score",
                    {"model_id": self._config.model_id, "pcm_base16": window.hex()},
                ),
                where="/wake/score",
            )
            score = float(_require_key(response, "score", where="/wake/score"))
            window_end_ms = self._consumed_bytes * 1000 // (SAMPLE_RATE_HZ * BYTES_PER_SAMPLE)
            if score >= self._threshold:
                if self._debounced:
                    continue
                self._streak += 1
                if self._streak >= self._min_activations:
                    phrase = str(response.get("phrase") or "haven")
                    events.append(WakeEvent(confidence=score, at_ms=window_end_ms, phrase=phrase))
                    self._debounced = True
                    self._streak = 0
            else:
                self._streak = 0
                self._debounced = False
        return events


class InferenceAsrProvider:
    """Streaming speech-to-text against `{base_url}/asr/accept|finalize`.

    Every `accept` chunk is POSTed honestly as it arrives --
    `{"model_id", "pcm_base16"}` -- and the endpoint's `partials` list maps
    to `TranscriptPartial`s. `finalize` POSTs `{"model_id"}` and maps the
    returned `{"text", "start_ms", "end_ms", "confidence"}` to a single
    `TranscriptFinal` (committed at `end_ms`). Endpoint failures raise
    `InferenceUnavailableError`: a caller must treat that as
    evidence-unavailable, never as an empty transcript.
    """

    def __init__(self, config: InferenceEndpointConfig) -> None:
        self._config = config

    def accept(self, pcm: bytes) -> list[TranscriptPartial | TranscriptFinal]:
        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        response = _require_mapping(
            _post_json(
                self._config,
                "/asr/accept",
                {"model_id": self._config.model_id, "pcm_base16": bytes(pcm).hex()},
            ),
            where="/asr/accept",
        )
        partials = response.get("partials") or []
        if not isinstance(partials, list):
            raise InferenceUnavailableError("/asr/accept response 'partials' must be a list")
        results: list[TranscriptPartial | TranscriptFinal] = []
        for item in partials:
            entry = _require_mapping(item, where="/asr/accept partial")
            results.append(
                TranscriptPartial(
                    text=str(_require_key(entry, "text", where="/asr/accept partial")),
                    at_ms=int(_require_key(entry, "at_ms", where="/asr/accept partial")),
                )
            )
        return results

    def finalize(self) -> list[TranscriptFinal]:
        response = _require_mapping(
            _post_json(self._config, "/asr/finalize", {"model_id": self._config.model_id}),
            where="/asr/finalize",
        )
        end_ms = int(_require_key(response, "end_ms", where="/asr/finalize"))
        return [
            TranscriptFinal(
                text=str(_require_key(response, "text", where="/asr/finalize")),
                start_ms=int(_require_key(response, "start_ms", where="/asr/finalize")),
                end_ms=end_ms,
                confidence=float(_require_key(response, "confidence", where="/asr/finalize")),
                at_ms=end_ms,
            )
        ]


class InferenceSynthesizer:
    """Interruptible text-to-speech against `{base_url}/tts`.

    `speak` POSTs `{"model_id", "text"}` and receives a JSON list of base16
    PCM chunks whose first element is the priming chunk, then yields them
    as bytes. `stop` sets a flag that truncates the stream at the next
    chunk -- no round trip, no model call -- which is the property barge-in
    depends on.
    """

    def __init__(self, config: InferenceEndpointConfig) -> None:
        self._config = config
        self._stop_requested = False

    def speak(self, text: str) -> Iterable[bytes]:
        _require_text(text, name="text")
        self._stop_requested = False
        response = _post_json(self._config, "/tts", {"model_id": self._config.model_id, "text": text})
        if not isinstance(response, list):
            raise InferenceUnavailableError("/tts response must be a JSON list of base16 PCM chunks")
        chunks: list[bytes] = []
        for item in response:
            if not isinstance(item, str):
                raise InferenceUnavailableError("/tts chunks must be base16 strings")
            try:
                chunks.append(bytes.fromhex(item))
            except ValueError as exc:
                raise InferenceUnavailableError(f"/tts chunk is not valid base16: {exc}") from exc

        def stream() -> Iterable[bytes]:
            for chunk in chunks:
                if self._stop_requested:
                    return
                yield chunk

        return stream()

    def stop(self) -> None:
        self._stop_requested = True


__all__ = [
    "InferenceAsrProvider",
    "InferenceEndpointConfig",
    "InferenceSynthesizer",
    "InferenceUnavailableError",
    "InferenceWakeDetector",
]
