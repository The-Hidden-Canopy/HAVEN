"""Manual smoke test: record from the real microphone, then play it back.

Run directly: `python scripts/smoke_mic_loopback.py [seconds]`

This exercises `WinMMMicrophoneSource`/`WinMMSpeakerSink`
(`haven.speech.native_audio`) against real hardware -- nothing in the
automated test suite can do this, since it needs an actual microphone and
speaker. It also writes the recording to a WAV file next to this script so
a human can listen back and confirm the captured audio is intelligible
(this script can confirm the plumbing moves real, non-degenerate bytes; it
cannot confirm the recording sounds correct).
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven.speech.events import FRAME_BYTES, SAMPLE_RATE_HZ
from haven.speech.native_audio import WinMMMicrophoneSource, WinMMSpeakerSink


def _amplitude_stats(pcm: bytes) -> tuple[int, int, float]:
    import struct

    if not pcm:
        return 0, 0, 0.0
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    peak = max(abs(s) for s in samples)
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    return min(samples), peak, rms


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    frames_needed = int(seconds * SAMPLE_RATE_HZ) // (FRAME_BYTES // 2)

    print(f"Recording {seconds:.1f}s from the default microphone...")
    mic = WinMMMicrophoneSource()
    mic.start()
    chunks: list[bytes] = []
    try:
        for i in range(frames_needed):
            frame = mic.read_frame()
            if not frame:
                print(f"  mic returned end-of-stream early at frame {i}")
                break
            chunks.append(frame)
    finally:
        mic.stop()

    pcm = b"".join(chunks)
    print(f"Captured {len(chunks)} frames, {len(pcm)} bytes.")
    low, peak, rms = _amplitude_stats(pcm)
    print(f"Amplitude: min={low} peak={peak} rms={rms:.1f} (int16 range is -32768..32767)")
    if peak == 0:
        print("WARNING: captured audio is pure digital silence -- check the microphone is not muted.")

    out_path = Path(__file__).resolve().parent / "smoke_mic_loopback.wav"
    with wave.open(str(out_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE_HZ)
        writer.writeframes(pcm)
    print(f"Wrote recording to {out_path}")

    print("Playing it back through the default speaker...")
    sink = WinMMSpeakerSink()
    sink.play([pcm])
    print("Playback finished. Listen to the WAV file above to confirm it sounds right.")


if __name__ == "__main__":
    main()
