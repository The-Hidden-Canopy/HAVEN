"""The model-handle shims: wire contract over scripted loaded-model handles.

The shims adapt role-assigned loaded model handles (the http/transformers/
llama_cpp reference-handle shapes) into the speech protocols. These tests
pin the wire payload shapes with fake handles that record every call:
how buffered PCM becomes one batched transcribe request, how a TTS chunk
envelope decodes prompt-first and truncates on a pure flag, and that a
wake scorer is only invoked once the rolling window is full. Missing
capabilities surface as typed `SpeechModelCapabilityError`, never as
guessed behavior.
"""

import pytest

from haven.models.results import InferenceResult
from haven.speech.events import FRAME_BYTES, TranscriptFinal, WakeEvent
from haven.speech.shims import (
    AsrModelRecognizer,
    SpeechModelCapabilityError,
    SpeechShims,
    TtsModelSynthesizer,
    WakeModelDetector,
    resolve_speech_shims,
)
from haven.speech.wake_model import KwsModelManifest

MODEL_ID = "asr-demo"


class _CapabilityHandle:
    """Records capability_method calls; serves scripted per-name payloads."""

    def __init__(self, payloads=None):
        self.calls = []
        self._payloads = dict(payloads or {})

    def capability_method(self, name, requires, payload):
        self.calls.append({"name": name, "requires": requires, "payload": payload})
        if name not in self._payloads:
            raise RuntimeError(f"no scripted capability {name!r}")
        return self._payloads[name]


class _DirectTranscribeHandle:
    """The plain-method shape: no capability_method, just transcribe."""

    def __init__(self, result):
        self.calls = []
        self._result = result

    def transcribe(self, audio_base16):
        self.calls.append(audio_base16)
        return self._result


def _frames(count: int, fill: int = 0x11) -> bytes:
    return bytes((fill,)) * FRAME_BYTES * count


# -- ASR ---------------------------------------------------------------------


def test_asr_finalize_returns_one_transcript_final():
    handle = _CapabilityHandle(
        {"transcribe": InferenceResult(outputs={"text": "turn off the porch light", "confidence": 0.87}, model_id=MODEL_ID)}
    )
    recognizer = AsrModelRecognizer(handle, model_id=MODEL_ID)

    assert recognizer.accept(_frames(4)) == []  # a batched model emits no partials
    finals = recognizer.finalize()

    assert len(finals) == 1
    final = finals[0]
    assert isinstance(final, TranscriptFinal)
    assert final.text == "turn off the porch light"
    assert final.confidence == pytest.approx(0.87)
    assert final.start_ms == 0
    assert final.end_ms == 100  # four 25 ms frames buffered
    assert final.at_ms == final.end_ms

    (call,) = handle.calls
    assert call["name"] == "transcribe"
    assert call["requires"] == {"asr"}
    sent = bytes.fromhex(call["payload"]["audio_base16"])
    assert sent == _frames(4)


def test_asr_finalize_is_single_shot_and_empty_buffers_stay_silent():
    handle = _CapabilityHandle(
        {"transcribe": InferenceResult(outputs={"text": "hello"}, model_id=MODEL_ID)}
    )
    recognizer = AsrModelRecognizer(handle, model_id=MODEL_ID)
    assert recognizer.finalize() == []  # nothing buffered: no model call
    recognizer.accept(_frames(2))
    assert len(recognizer.finalize()) == 1
    recognizer.accept(_frames(2))
    assert recognizer.finalize() == []  # already finalized
    assert len(handle.calls) == 1


def test_asr_tolerant_result_keys_and_confidence_clamp():
    for key in ("result", "transcript"):
        handle = _CapabilityHandle({"transcribe": InferenceResult(outputs={key: "  hello there "}, model_id=MODEL_ID)})
        recognizer = AsrModelRecognizer(handle, model_id=MODEL_ID)
        recognizer.accept(_frames(1))
        (final,) = recognizer.finalize()
        assert final.text == "hello there"

    handle = _CapabilityHandle(
        {"transcribe": InferenceResult(outputs={"text": "loud", "confidence": 4.5}, model_id=MODEL_ID)}
    )
    recognizer = AsrModelRecognizer(handle, model_id=MODEL_ID)
    recognizer.accept(_frames(1))
    (final,) = recognizer.finalize()
    assert final.confidence == 1.0  # clamped, not trusted


def test_asr_accepts_plain_dict_result_envelope():
    handle = _CapabilityHandle({"transcribe": {"text": "dict envelope", "confidence": 0.5}})
    recognizer = AsrModelRecognizer(handle, model_id=MODEL_ID)
    recognizer.accept(_frames(1))
    (final,) = recognizer.finalize()
    assert final.text == "dict envelope"


def test_asr_falls_back_to_direct_transcribe_method():
    handle = _DirectTranscribeHandle({"result": "direct method"})
    recognizer = AsrModelRecognizer(handle, model_id=MODEL_ID)
    recognizer.accept(_frames(2))
    (final,) = recognizer.finalize()
    assert final.text == "direct method"
    assert bytes.fromhex(handle.calls[0]) == _frames(2)


def test_asr_missing_capability_raises_typed_error():
    class _Bare:
        pass

    with pytest.raises(SpeechModelCapabilityError) as excinfo:
        AsrModelRecognizer(_Bare(), model_id=MODEL_ID)
    assert "asr" in str(excinfo.value)
    assert issubclass(SpeechModelCapabilityError, RuntimeError)

    with pytest.raises(SpeechModelCapabilityError):
        TtsModelSynthesizer(_Bare())


# -- TTS ---------------------------------------------------------------------


def test_tts_decodes_chunks_prompt_first():
    handle = _CapabilityHandle({"tts": {"chunks_base16": ["ff00", "0102", "0304"]}})
    synthesizer = TtsModelSynthesizer(handle)

    chunks = list(synthesizer.speak("hello"))

    assert chunks == [b"\xff\x00", b"\x01\x02", b"\x03\x04"]  # first chunk is the prompt
    (call,) = handle.calls
    assert call["name"] == "tts"
    assert call["requires"] == {"tts"}
    assert call["payload"] == {"text": "hello"}


def test_tts_accepts_bare_chunk_list_envelope():
    handle = _CapabilityHandle({"tts": ["aa00", "bb11"]})
    synthesizer = TtsModelSynthesizer(handle)
    assert list(synthesizer.speak("hi")) == [b"\xaa\x00", b"\xbb\x11"]


def test_tts_stop_truncates_without_further_calls():
    handle = _CapabilityHandle({"tts": {"chunks_base16": ["00", "11", "22", "33"]}})
    synthesizer = TtsModelSynthesizer(handle)
    stream = iter(synthesizer.speak("long utterance"))
    assert next(stream) == b"\x00"
    synthesizer.stop()  # pure flag: no round trip, no extra model call
    assert list(stream) == []
    assert len(handle.calls) == 1


def test_tts_stop_resets_on_the_next_speak():
    handle = _CapabilityHandle({"tts": {"chunks_base16": ["00", "11"]}})
    synthesizer = TtsModelSynthesizer(handle)
    stream = iter(synthesizer.speak("one"))
    next(stream)
    synthesizer.stop()
    assert list(stream) == []
    assert list(synthesizer.speak("two")) == [b"\x00", b"\x11"]


# -- Wake --------------------------------------------------------------------


def _wake_manifest() -> KwsModelManifest:
    return KwsModelManifest(
        model_id="haven-kws",
        version="1.0",
        window_ms=100,  # four frames: the window fills quickly in tests
        threshold=0.5,
        debounce_ms=0,
    )


def test_wake_scorer_called_only_once_window_is_full():
    handle = _CapabilityHandle({"wake_score": 0.9})
    detector = WakeModelDetector(handle, _wake_manifest())

    assert detector.process(_frames(3, fill=0x22)) == []
    assert handle.calls == []  # lazy: the window is not full yet

    events = detector.process(_frames(1, fill=0x22))
    assert len(events) == 1
    assert isinstance(events[0], WakeEvent)
    assert events[0].phrase == "haven"

    (call,) = handle.calls
    assert call["name"] == "wake_score"
    assert call["requires"] == {"wake_word"}
    window = bytes.fromhex(call["payload"]["window_base16"])
    assert window == _frames(4, fill=0x22)  # exactly one full window


def test_wake_below_threshold_fires_nothing_and_re_arms():
    handle = _CapabilityHandle({"wake_score": 0.1})
    detector = WakeModelDetector(handle, _wake_manifest())
    assert detector.process(_frames(8)) == []
    assert len(handle.calls) == 5  # frames 4..8 each scored one full window


def test_wake_uses_default_manifest_when_none_given():
    handle = _CapabilityHandle({"wake_score": 0.0})
    detector = WakeModelDetector(handle)
    assert detector.manifest is not None
    assert detector.manifest.model_id == "haven-kws"


# -- resolve -----------------------------------------------------------------


def test_resolve_speech_shims_adapts_each_supplied_handle():
    wake = _CapabilityHandle({"wake_score": 0.0})
    asr = _CapabilityHandle({"transcribe": {"text": "x"}})
    tts = _CapabilityHandle({"tts": {"chunks_base16": ["00"]}})

    shims = resolve_speech_shims(wake_handle=wake, asr_handle=asr, tts_handle=tts)

    assert isinstance(shims, SpeechShims)
    assert isinstance(shims.wake, WakeModelDetector)
    assert isinstance(shims.asr, AsrModelRecognizer)
    assert isinstance(shims.tts, TtsModelSynthesizer)


def test_resolve_speech_shims_leaves_absent_roles_none():
    shims = resolve_speech_shims()
    assert shims.wake is None
    assert shims.asr is None
    assert shims.tts is None
