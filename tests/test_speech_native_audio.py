"""WinMM native audio: real capture/playback mechanics, never audio content.

Mirrors `test_bluetooth_winrt_backend.py`'s discipline: these tests prove
the plumbing (exact frame accounting, prompt truncation on stop) works
against whatever real audio device is on the host machine, and deliberately
never assert anything about audio *content* -- what a microphone actually
picks up is a fact about the room at test time, not about this code, the
same reason the Bluetooth WinRT tests assert nothing about scan results.

Skipped entirely off Windows, or on a Windows host with no input/output
device (a CI runner, most likely).
"""

from __future__ import annotations

import platform
import struct
import time

import pytest

from haven.speech.events import FRAME_BYTES, SAMPLE_RATE_HZ
from haven.speech.native_audio import WinMMError, WinMMMicrophoneSource, WinMMSpeakerSink, _pcm_format


def _audio_available() -> bool:
    if platform.system() != "Windows":
        return False
    from haven.speech.native_audio import winmm

    return winmm.waveInGetNumDevs() > 0 and winmm.waveOutGetNumDevs() > 0


pytestmark = pytest.mark.skipif(
    not _audio_available(), reason="no Windows audio input/output device on this host"
)


def test_pcm_format_matches_the_pinned_wire_contract() -> None:
    fmt = _pcm_format()
    assert fmt.nSamplesPerSec == SAMPLE_RATE_HZ
    assert fmt.wBitsPerSample == 16
    assert fmt.nChannels == 1
    assert fmt.nBlockAlign == 2
    assert fmt.nAvgBytesPerSec == SAMPLE_RATE_HZ * 2


def test_microphone_capture_yields_exact_frame_counts() -> None:
    mic = WinMMMicrophoneSource()
    mic.start()
    try:
        frames = [mic.read_frame() for _ in range(20)]
    finally:
        mic.stop()
    assert len(frames) == 20
    for frame in frames:
        assert isinstance(frame, bytes)
        assert len(frame) == FRAME_BYTES


def test_microphone_stop_then_read_returns_end_of_stream() -> None:
    mic = WinMMMicrophoneSource()
    mic.start()
    mic.read_frame()
    mic.stop()
    assert mic.read_frame() == b""


def test_microphone_can_restart_after_stop() -> None:
    mic = WinMMMicrophoneSource()
    mic.start()
    mic.read_frame()
    mic.stop()
    mic.start()
    try:
        frame = mic.read_frame()
        assert len(frame) == FRAME_BYTES
    finally:
        mic.stop()


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE_HZ * seconds)


def test_speaker_play_completes_for_a_short_clip() -> None:
    sink = WinMMSpeakerSink()
    started = time.monotonic()
    sink.play([_silence(0.2)])
    elapsed = time.monotonic() - started
    # Real playback time, not instant -- but bounded well above the clip's
    # own duration only by scheduling slack, never by a hang.
    assert elapsed < 3.0


def test_speaker_stop_truncates_playback_promptly() -> None:
    sink = WinMMSpeakerSink()
    long_clip = _silence(4.0)

    import threading

    def stopper() -> None:
        time.sleep(0.3)
        sink.stop()

    thread = threading.Thread(target=stopper)
    started = time.monotonic()
    thread.start()
    sink.play([long_clip])
    elapsed = time.monotonic() - started
    thread.join()
    assert elapsed < 2.0, f"stop() did not truncate a 4s clip promptly (took {elapsed:.2f}s)"


def test_speaker_stop_before_play_is_a_harmless_flag() -> None:
    sink = WinMMSpeakerSink()
    sink.stop()  # nothing playing yet -- must not raise
    sink.play([_silence(0.1)])
