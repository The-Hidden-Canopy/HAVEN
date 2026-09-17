"""EnergyVad tests over synthetic 16 kHz 16-bit LE PCM."""

import math
import struct

import pytest

from haven.speech import (
    FRAME_MS,
    SAMPLES_PER_FRAME,
    SAMPLE_RATE_HZ,
    SpeechContinued,
    SpeechEnded,
    SpeechStarted,
)
from haven.speech.vad import EnergyVad


def _sine_frame(amplitude: float = 10000.0, frequency: float = 440.0, start_sample: int = 0) -> bytes:
    samples = [
        int(amplitude * math.sin(2.0 * math.pi * frequency * (start_sample + i) / SAMPLE_RATE_HZ))
        for i in range(SAMPLES_PER_FRAME)
    ]
    return struct.pack(f"<{SAMPLES_PER_FRAME}h", *samples)


def _silence_frame() -> bytes:
    return b"\x00\x00" * SAMPLES_PER_FRAME


def _stream(silence_frames: int, speech_frames: int, tail_frames: int) -> bytes:
    chunks = [_silence_frame() for _ in range(silence_frames)]
    chunks += [_sine_frame(start_sample=i * SAMPLES_PER_FRAME) for i in range(speech_frames)]
    chunks += [_silence_frame() for _ in range(tail_frames)]
    return b"".join(chunks)


def test_silence_produces_no_events():
    vad = EnergyVad()

    events = vad.process(_stream(silence_frames=40, speech_frames=0, tail_frames=0))

    assert events == []


def test_sine_utterance_yields_started_continued_ended_without_flicker():
    vad = EnergyVad()

    events = vad.process(_stream(silence_frames=10, speech_frames=10, tail_frames=20))

    started = [event for event in events if isinstance(event, SpeechStarted)]
    ended = [event for event in events if isinstance(event, SpeechEnded)]
    continued = [event for event in events if isinstance(event, SpeechContinued)]
    assert len(started) == 1
    assert len(ended) == 1
    assert continued, "expected SpeechContinued events during the utterance"
    # No flicker: exactly one onset and it precedes the single end.
    assert events.index(started[0]) < events.index(ended[0])
    assert started[0].at_ms < ended[0].at_ms
    # Onset debounce holds the start at the first candidate frame, not the
    # fourth: min_speech_ms=100 / frame 25 ms -> onset reported at the
    # first voiced frame.
    assert started[0].at_ms == 10 * FRAME_MS
    # Hold time keeps SpeechEnded ~300 ms after the last voiced frame.
    assert ended[0].at_ms == (10 + 10 + 11) * FRAME_MS
    assert ended[0].duration_ms == ended[0].at_ms - started[0].at_ms
    # Every continued event sits inside the utterance interval.
    assert all(started[0].at_ms <= event.at_ms < ended[0].at_ms for event in continued)


def test_chunked_processing_matches_single_shot():
    vad = EnergyVad()
    whole = _stream(silence_frames=10, speech_frames=10, tail_frames=20)

    expected = vad.process(whole)

    vad_chunked = EnergyVad()
    actual = []
    for offset in range(0, len(whole), 2401):  # deliberately awkward chunk size
        actual.extend(vad_chunked.process(whole[offset:offset + 2401]))

    assert actual == expected


def test_tails_are_buffered_until_a_full_frame_arrives():
    vad = EnergyVad()
    half = _sine_frame()[: len(_sine_frame()) // 2]

    assert vad.process(half) == []
    assert vad.process(half) == []  # one full frame buffered -> candidate, no event yet


def test_non_integer_chunk_is_rejected():
    vad = EnergyVad()

    events = vad.process(_stream(silence_frames=5, speech_frames=6, tail_frames=16))

    assert len([e for e in events if isinstance(e, SpeechStarted)]) == 1
    assert len([e for e in events if isinstance(e, SpeechEnded)]) == 1


def test_parameters_are_validated():
    with pytest.raises(ValueError):
        EnergyVad(onset_db=0)
    with pytest.raises(ValueError):
        EnergyVad(release_db=20.0, onset_db=12.0)
    with pytest.raises(ValueError):
        EnergyVad(hold_ms=-1)
    with pytest.raises(ValueError):
        EnergyVad(min_speech_ms=10)
