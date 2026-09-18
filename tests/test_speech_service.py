"""The speech service: real audio pumped synchronously through the pipeline.

A recorded WAV file (stdlib `wave` + `struct` sine/silence) drives the
whole vertical-slice runtime -- no microphone. The tests pin the service
state machine (dormant|wake|listening|interpreting|speaking), the
always-on and push-to-talk modes, the barge-in hard requirement (sink
sees stop before all chunks, no model call), the TTS artifact (a valid
WAV of the synthesized chunks), and the honest NoSpeechModelError when an
utterance completes with no recognizer wired.
"""

import math
import struct
import tempfile
import wave
from pathlib import Path

import pytest

from haven.models.results import InferenceResult
from haven.speech import (
    FRAME_BYTES,
    ScriptedSynthesizer,
    ScriptedVad,
    ScriptedWakeDetector,
    SilenceSource,
    SpeechService,
    TranscriptFinal,
    WavFileSink,
    WavFileSource,
)
from haven.speech.service import NoSpeechModelError
from haven.speech.shims import AsrModelRecognizer


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _sine_pcm(frames: int, *, amplitude: int = 8000, frequency: float = 440.0) -> bytes:
    out = bytearray()
    for index in range(frames * 400):
        sample = int(amplitude * math.sin(2.0 * math.pi * frequency * index / 16000.0))
        out += struct.pack("<h", sample)
    return bytes(out)


def _write_wav(path: Path, pcm: bytes, *, rate: int = 16000, channels: int = 1, width: int = 2) -> Path:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return path


def _pump_all(service: SpeechService, source) -> None:
    source.start()
    try:
        while True:
            frame = source.read_frame()
            if not frame:
                break
            service.pump_frame(frame)
    finally:
        source.stop()


class _ScriptedAsrHandle:
    """A loaded-model handle shape serving one scripted transcription."""

    def __init__(self, text: str, *, confidence: float = 0.9) -> None:
        self.calls = []
        self._result = InferenceResult(
            outputs={"text": text, "confidence": confidence}, model_id="asr-demo"
        )

    def capability_method(self, name, requires, payload):
        self.calls.append({"name": name, "requires": requires, "payload": payload})
        return self._result


# -- sources -----------------------------------------------------------------


def test_wav_file_source_reads_exact_frames(workdir):
    pcm = _sine_pcm(10)
    source = WavFileSource(_write_wav(workdir / "input.wav", pcm))
    source.start()
    try:
        for _ in range(10):
            assert len(source.read_frame()) == FRAME_BYTES
        assert source.read_frame() == b""  # end of stream
    finally:
        source.stop()


def test_wav_file_source_rejects_wrong_wire_format(workdir):
    good = _sine_pcm(1)
    with pytest.raises(ValueError, match="sample rate"):
        WavFileSource(_write_wav(workdir / "rate.wav", good, rate=8000))
    with pytest.raises(ValueError, match="channel"):
        WavFileSource(_write_wav(workdir / "stereo.wav", good, channels=2))
    with pytest.raises(ValueError, match="sample width"):
        WavFileSource(_write_wav(workdir / "width.wav", good, width=1))


def test_silence_source_yields_frames_then_end():
    source = SilenceSource(3)
    source.start()
    try:
        assert [len(source.read_frame()) for _ in range(3)] == [FRAME_BYTES] * 3
        assert source.read_frame() == b""
    finally:
        source.stop()


# -- the always-on pipeline over real audio -----------------------------------


def _recording() -> bytes:
    # 8 silence frames, 20 voiced frames, 16 silence frames: the trailing
    # silence exceeds EnergyVad's 300 ms hold, so SpeechEnded is sample-driven.
    return b"\x00" * FRAME_BYTES * 8 + _sine_pcm(20) + b"\x00" * FRAME_BYTES * 16


def test_always_on_pipeline_wake_to_final(workdir):
    source = WavFileSource(_write_wav(workdir / "utterance.wav", _recording()))
    handle = _ScriptedAsrHandle("haven, turn off the porch light", confidence=0.91)
    utterances: list[TranscriptFinal] = []
    states: list[str] = []
    service = SpeechService(
        source=source,
        wake=ScriptedWakeDetector(triggers_at_frame=9),
        recognizer=_asr_shim(handle),
        sink=_NullSink(),
        on_utterance=utterances.append,
        on_speech_state=states.append,
    )

    _pump_all(service, source)
    service.stop()

    assert len(utterances) == 1
    final = utterances[0]
    assert final.text == "haven, turn off the porch light"
    assert final.confidence == pytest.approx(0.91)
    assert final.end_ms > 0
    # one batched transcribe call over the buffered post-wake audio
    assert len(handle.calls) == 1
    assert handle.calls[0]["payload"]["audio_base16"]
    assert states == ["wake", "listening", "interpreting", "dormant"]


def _asr_shim(handle) -> AsrModelRecognizer:
    return AsrModelRecognizer(handle, model_id="asr-demo")


class _NullSink:
    def play(self, chunks) -> None:
        for _ in chunks:
            pass

    def stop(self) -> None:
        pass


# -- push-to-talk -------------------------------------------------------------


def test_push_to_talk_opens_the_session_explicitly(workdir):
    source = WavFileSource(_write_wav(workdir / "ptt.wav", _sine_pcm(12)))
    handle = _ScriptedAsrHandle("porch light off")
    utterances: list[TranscriptFinal] = []
    states: list[str] = []
    service = SpeechService(
        source=source,
        vad=ScriptedVad(script=[(0, 7)]),
        recognizer=_asr_shim(handle),
        on_utterance=utterances.append,
        on_speech_state=states.append,
    )

    service.begin_utterance()
    assert states == ["wake", "listening"]
    _pump_all(service, source)
    service.stop()

    assert [final.text for final in utterances] == ["porch light off"]
    assert states[-2:] == ["interpreting", "dormant"]


# -- barge-in ------------------------------------------------------------------


def test_barge_in_truncates_a_slow_sink_stream():
    sink = _RecordingSink()
    service = SpeechService(
        source=SilenceSource(0),
        synthesizer=ScriptedSynthesizer(chunk_count=1000, chunk_bytes=FRAME_BYTES),
        sink=sink,
    )

    played = 0

    def slow_play(chunks) -> None:
        nonlocal played
        for chunk in chunks:
            played += 1
            if played >= 5:
                # the service interrupts mid-stream; the sink must observe
                # stop before the remaining ~995 chunks
                service.barge_in()
            if sink.stopped:
                break

    sink.play = slow_play
    service.say("a very long reply")

    assert sink.stopped
    assert played < 1000


def test_stop_speaking_is_safe_with_nothing_in_flight():
    service = SpeechService(source=SilenceSource(0))
    service.barge_in()
    service.stop_speaking()
    service.stop()


# -- TTS artifact ---------------------------------------------------------------


def test_say_writes_a_valid_wav_of_the_tts_chunks(workdir):
    out_path = workdir / "reply.wav"
    service = SpeechService(
        source=SilenceSource(0),
        synthesizer=ScriptedSynthesizer(chunk_count=6, chunk_bytes=FRAME_BYTES),
        sink=WavFileSink(out_path),
    )
    service.say("the porch light is off")
    service.stop()

    with wave.open(str(out_path), "rb") as reader:
        assert reader.getframerate() == 16000
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getnframes() == 6 * 400  # the priming chunk is empty


def test_say_without_tts_is_a_documented_no_op():
    service = SpeechService(source=SilenceSource(0))
    service.say("nothing is wired")  # must not raise
    service.stop()


# -- honest degradation ----------------------------------------------------------


def test_utterance_without_recognizer_raises_and_closes_cleanly(workdir):
    source = WavFileSource(_write_wav(workdir / "noasr.wav", _recording()))
    service = SpeechService(source=source, wake=ScriptedWakeDetector(triggers_at_frame=9))

    with pytest.raises(NoSpeechModelError):
        _pump_all(service, source)
    service.stop()  # the session still closes cleanly
    assert service.session.state.value == "closed"


def test_daemon_thread_pump_runs_the_same_loop(workdir):
    source = WavFileSource(_write_wav(workdir / "thread.wav", _recording()))
    handle = _ScriptedAsrHandle("threaded pump works")
    utterances: list[TranscriptFinal] = []
    service = SpeechService(
        source=source,
        wake=ScriptedWakeDetector(triggers_at_frame=9),
        recognizer=_asr_shim(handle),
        on_utterance=utterances.append,
    )
    thread = service.start()
    thread.join(timeout=10.0)
    service.stop()

    assert not thread.is_alive()
    assert service.pump_error is None
    assert [final.text for final in utterances] == ["threaded pump works"]


class _RecordingSink:
    def __init__(self) -> None:
        self.stopped = False

    def play(self, chunks) -> None:
        for _ in chunks:
            pass

    def stop(self) -> None:
        self.stopped = True
