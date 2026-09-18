"""Shims: protocol-conforming adapters over LOADED model handles.

The Model Manager's bridge (`haven.models.bridge`) resolves speech-capable
models to role-assigned loaded handles; those handles speak the model
backends' own dialect -- `capability_method(name, requires, payload)`
envelopes on the http backend, plain methods like `transcribe` /
`synthesize` elsewhere -- not the streaming provider protocols of
`haven.speech.protocols`. These shims are the honest seam between the two:
they turn a stateless, batched model call into a streaming protocol
implementation, and they do it with zero dependencies.

Doctrine: handles are role-assigned loaded models from the ModelManager
bridge; HAVEN never bundles the models. A shim is thin on purpose -- it
adapts the wire contract and nothing more:

- ASR buffers whole frames of PCM and sends the buffered utterance to the
  model exactly once, at `finalize()`. A model that cannot stream does not
  pretend to: `accept` returns no partials.
- TTS decodes the model's base16 chunk envelopes; the first chunk is the
  prompt so playback starts immediately. `stop()` is a pure flag checked
  between chunks -- truncating a spoken response must never cost a model
  or LLM round trip (a barge-in must land within one chunk).
- KWS is lazy by construction: the scorer is only invoked once the
  rolling window is full, which `ThresholdedWakeDetector` already gates.

Missing capabilities are surfaced, never guessed: calling a shim whose
handle cannot serve its role raises `SpeechModelCapabilityError`, a typed
`RuntimeError` naming the capability.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .events import BYTES_PER_SAMPLE, FRAME_BYTES, TranscriptFinal
from .protocols import SpeechRecognizer, SpeechSynthesizer, WakeDetector
from .wake_model import KwsModelManifest, ThresholdedWakeDetector

# The default contract a handle-adapted wake artifact is served under when
# the manager has no richer manifest: the reference HAVEN-KWS shape.
_DEFAULT_KWS_MANIFEST = KwsModelManifest(model_id="haven-kws", version="1.0")


class SpeechModelCapabilityError(RuntimeError):
    """A loaded model handle cannot serve the speech capability a shim needs.

    Raised eagerly, at shim construction, so a mis-assigned model fails at
    wiring time instead of mid-utterance. The message names the missing
    capability and the fallback the handle does advertise (if any).
    """


@dataclass(frozen=True)
class SpeechShims:
    """The three protocol adapters for one wiring of loaded model handles.

    A field is None when no handle was supplied for that role; callers
    treat an absent shim as "this role is not model-backed" and route
    around it (fixtures, push-to-talk, silent TTS).
    """

    wake: WakeDetector | None
    asr: SpeechRecognizer | None
    tts: SpeechSynthesizer | None


def _require_handle(handle: Any, *, role: str) -> Any:
    if handle is None:
        raise ValueError(f"{role} handle must not be None")
    return handle


def _call_capability(
    handle: Any,
    method: str,
    capability: str,
    payload: dict[str, Any],
    *,
    role: str,
    direct_kwarg: str | None = None,
) -> Any:
    """One model invocation: capability-gated envelope, else a direct method.

    `capability_method` wins when present (the http handle shape); a direct
    method (`transcribe`, `synthesize`) is the fallback for handle shapes
    that expose one. Either path is a missing capability when absent.
    """

    capability_method = getattr(handle, "capability_method", None)
    if callable(capability_method):
        return capability_method(method, requires={capability}, payload=payload)
    direct = getattr(handle, method, None)
    if callable(direct):
        if direct_kwarg is not None:
            return direct(**{direct_kwarg: payload[direct_kwarg]})
        return direct(payload)
    raise SpeechModelCapabilityError(
        f"{role} handle {type(handle).__name__} cannot serve capability {capability!r}: "
        f"no 'capability_method' and no '{method}' method"
    )


def _outputs_of(result: Any) -> dict[str, Any]:
    """The plain-data outputs mapping of an InferenceResult or dict, defensively."""

    if hasattr(result, "outputs"):
        outputs = getattr(result, "outputs")
        if isinstance(outputs, dict):
            return outputs
    if isinstance(result, dict):
        return result
    raise ValueError(
        f"model returned an unrecognized result envelope ({type(result).__name__}); "
        "expected an InferenceResult or a dict"
    )


def _transcript_text(outputs: dict[str, Any]) -> str:
    """The transcript text, tolerant of the envelopes backends actually return."""

    for key in ("text", "result", "transcript"):
        value = outputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("model transcription payload carries no 'text', 'result', or 'transcript'")


def _chunk_list(payload: Any) -> list[Any]:
    """The TTS chunk envelopes: a {"chunks_base16": [...]} mapping or a bare list."""

    if isinstance(payload, dict):
        chunks = payload.get("chunks_base16")
        if isinstance(chunks, list):
            return chunks
        raise ValueError("model TTS payload has no 'chunks_base16' list")
    if isinstance(payload, list):
        return payload
    raise ValueError(
        f"model returned an unrecognized TTS envelope ({type(payload).__name__}); "
        "expected a mapping with 'chunks_base16' or a list of base16 strings"
    )


class AsrModelRecognizer:
    """`SpeechRecognizer` over a loaded asr model handle.

    `accept` buffers whole 25 ms frames; a stateless, non-streaming model
    has no honest partials, so `accept` returns none. `finalize` sends the
    buffered audio once -- hex-encoded PCM under the "transcribe"
    capability, or a direct `transcribe` method -- and maps the result to
    one `TranscriptFinal` spanning the buffered duration.
    """

    def __init__(self, handle: Any, *, model_id: str) -> None:
        _require_handle(handle, role="asr")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        self._handle = handle
        self._model_id = model_id.strip()
        self._buffer = bytearray()
        self._finalized = False
        # A handle with neither invocation path cannot serve asr at all:
        # fail at wiring time, not mid-utterance.
        if not callable(getattr(handle, "capability_method", None)) and not callable(
            getattr(handle, "transcribe", None)
        ):
            raise SpeechModelCapabilityError(
                f"asr handle {type(handle).__name__} cannot serve capability 'asr': "
                "no 'capability_method' and no 'transcribe' method"
            )

    @property
    def model_id(self) -> str:
        return self._model_id

    def accept(self, pcm: bytes) -> list[Any]:
        """Buffer one chunk of PCM; a batched model emits no partials."""

        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        data = bytes(pcm)
        self._buffer += data[: len(data) - len(data) % FRAME_BYTES]
        return []

    def finalize(self) -> list[Any]:
        """Send the buffered utterance once and commit it as one final."""

        from .events import TranscriptFinal

        if self._finalized:
            return []
        buffered = bytes(self._buffer)
        self._buffer.clear()
        if not buffered:
            return []  # nothing to send; the single finalize is not spent
        self._finalized = True
        result = _call_capability(
            self._handle,
            "transcribe",
            "asr",
            {"audio_base16": buffered.hex()},
            role="asr",
            direct_kwarg="audio_base16",
        )
        outputs = _outputs_of(result)
        duration_ms = len(buffered) * 1000 // (16000 * BYTES_PER_SAMPLE)
        raw_confidence = outputs.get("confidence", 1.0)
        if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
            raw_confidence = 1.0
        return [
            TranscriptFinal(
                text=_transcript_text(outputs),
                start_ms=0,
                end_ms=duration_ms,
                confidence=min(1.0, max(0.0, float(raw_confidence))),
                at_ms=duration_ms,
            )
        ]


class TtsModelSynthesizer:
    """`SpeechSynthesizer` over a loaded tts model handle.

    `speak` invokes the model once and yields the returned PCM chunks
    decoded from base16, first chunk first (the prompt: playback starts
    before synthesis would otherwise complete). `stop` is a pure flag
    checked between chunks -- truncation never costs a model call.
    """

    def __init__(self, handle: Any) -> None:
        _require_handle(handle, role="tts")
        self._handle = handle
        self._stop_requested = False
        if not callable(getattr(handle, "capability_method", None)) and not callable(
            getattr(handle, "synthesize", None)
        ):
            raise SpeechModelCapabilityError(
                f"tts handle {type(handle).__name__} cannot serve capability 'tts': "
                "no 'capability_method' and no 'synthesize' method"
            )

    def speak(self, text: str) -> Iterable[bytes]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        self._stop_requested = False
        result = _call_capability(
            self._handle,
            "tts",
            "tts",
            {"text": text.strip()},
            role="tts",
            direct_kwarg="text",
        )

        def stream() -> Iterable[bytes]:
            for chunk in _chunk_list(result):
                if self._stop_requested:
                    return
                if not isinstance(chunk, str):
                    raise ValueError("model TTS chunk is not a base16 string")
                yield bytes.fromhex(chunk)

        return stream()

    def stop(self) -> None:
        self._stop_requested = True


class WakeModelDetector:
    """`WakeDetector` composing `ThresholdedWakeDetector` over a loaded kws handle.

    The scorer calls the handle's "wake_score" capability with the rolling
    window as hex PCM and reads a float probability back. Laziness is
    inherited: the detector only invokes the scorer once the advertised
    window is full, so a fixed-shape model never sees an undersized window.
    """

    def __init__(self, handle: Any, manifest: KwsModelManifest | None = None) -> None:
        _require_handle(handle, role="wake")
        if manifest is not None and not isinstance(manifest, KwsModelManifest):
            raise ValueError("manifest must be a KwsModelManifest or None")
        self._handle = handle
        self._manifest = manifest if manifest is not None else _DEFAULT_KWS_MANIFEST
        self._calls: list[bytes] = []
        self._detector = ThresholdedWakeDetector(self._manifest, self._score)

    @property
    def manifest(self) -> KwsModelManifest:
        return self._manifest

    @property
    def scorer_calls(self) -> tuple[bytes, ...]:
        """Every window the handle's scorer was actually invoked with, in order."""

        return tuple(self._calls)

    def process(self, pcm: bytes) -> list[Any]:
        return self._detector.process(pcm)

    def _score(self, window_pcm: bytes) -> float:
        self._calls.append(window_pcm)
        result = _call_capability(
            self._handle,
            "wake_score",
            "wake_word",
            {"window_base16": window_pcm.hex()},
            role="wake",
            direct_kwarg="window_base16",
        )
        if isinstance(result, bool) or not isinstance(result, (int, float)):
            raise ValueError("wake_score must return a number between 0.0 and 1.0")
        return min(1.0, max(0.0, float(result)))


def resolve_speech_shims(
    *,
    wake_handle: Any = None,
    asr_handle: Any = None,
    tts_handle: Any = None,
    manifest: KwsModelManifest | None = None,
) -> SpeechShims:
    """Adapt role-assigned loaded model handles into the speech protocols.

    Each handle comes from the ModelManager bridge (`asr_handle()`,
    `tts_handle()`, or a loaded wake-word model's `loaded_handle`) -- a
    role-assigned loaded model; HAVEN never bundles the models themselves.
    A role with no handle yields a None shim, so callers route around
    absent models instead of failing.
    """

    return SpeechShims(
        wake=WakeModelDetector(wake_handle, manifest) if wake_handle is not None else None,
        asr=AsrModelRecognizer(asr_handle, model_id="asr") if asr_handle is not None else None,
        tts=TtsModelSynthesizer(tts_handle) if tts_handle is not None else None,
    )


__all__ = [
    "AsrModelRecognizer",
    "SpeechModelCapabilityError",
    "SpeechShims",
    "TtsModelSynthesizer",
    "WakeModelDetector",
    "resolve_speech_shims",
]
