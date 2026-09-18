"""ctypes binding to WinMM (winmm.dll) for real microphone capture and
speaker playback.

`WinMMMicrophoneSource` and `WinMMSpeakerSink` are HAVEN's actual live-audio
`AudioSource`/`PlaybackSink` -- the real hardware behind the same protocols
`WavFileSource`/`WavFileSink` (`haven/speech/sources.py`, `sinks.py`) already
satisfy with recorded files. `SpeechService` and everything upstream of it
(wake detection, VAD, ASR, TTS, the session state machine) do not change at
all to use these: they were already written against the protocol, not the
file-backed fixture.

WinMM's `waveIn*`/`waveOut*` functions are a flat, `__stdcall` C API with no
COM vtables (unlike WASAPI) -- the same reason this project prefers a native
ctypes ABI over a Python audio package (`sounddevice`, `pyaudio`) for
Bluetooth: it is real OS API access with zero third-party dependencies,
matching `haven.integrations.bluetooth.native`'s approach. This is the only
file in `haven/speech` that touches `ctypes.WinDLL`; every other module in
the package is platform-agnostic and knows nothing about WinMM.

Windows-only: `winmm.dll` does not exist elsewhere. Importing this module on
another platform is safe (nothing touches `ctypes.WinDLL` at import time),
but constructing either class raises `WinMMError` immediately. Linux/macOS
backends (ALSA/PulseAudio, CoreAudio) are not built here -- the same
documented gap `native/haven-bt/README.md` already carries for Bluetooth on
those platforms.

This binding has not been exercised against real audio hardware from this
session -- there is no microphone or speaker attached to verify against.
`scripts/smoke_mic_loopback.py` is a self-contained record-then-playback
script for a human to run and confirm the capture/playback path actually
works before anything in `haven/web` is wired to depend on it.
"""

from __future__ import annotations

import ctypes
import platform
import queue
import threading
from ctypes import wintypes
from typing import Iterable

from .events import FRAME_BYTES, SAMPLE_RATE_HZ

_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - exercised only by not being Windows
    winmm = None
    kernel32 = None

WAVE_FORMAT_PCM = 1
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_EVENT = 0x00050000
WHDR_DONE = 0x00000001
MMSYSERR_NOERROR = 0
WAIT_TIMEOUT = 0x00000102
_ERROR_TEXT_LEN = 256


class WAVEFORMATEX(ctypes.Structure):
    """Mirrors `WAVEFORMATEX` in mmreg.h field-for-field."""

    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    """Mirrors `WAVEHDR` in mmsystem.h field-for-field.

    `lpData` is deliberately `c_void_p`, not `c_char_p`: ctypes reads a
    `c_char_p` field back as a NUL-terminated Python `bytes`, which would
    silently truncate any captured frame containing a zero byte -- a real
    possibility in raw PCM (digital silence is all zero bytes). The actual
    buffer is a separately-held `create_string_buffer`; this field only
    carries its address for the driver.
    """

    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


class WinMMError(RuntimeError):
    """Raised when a WinMM call fails, or on a non-Windows host."""

    def __init__(self, function: str, detail: int | str) -> None:
        if isinstance(detail, int):
            message = f"{function} failed: {_error_text(detail)} (MMRESULT {detail})"
        else:
            message = f"{function} failed: {detail}"
        super().__init__(message)
        self.function = function
        self.detail = detail


def _error_text(mmresult: int) -> str:
    if not _IS_WINDOWS:
        return "unknown error"
    buf = ctypes.create_unicode_buffer(_ERROR_TEXT_LEN)
    if winmm.waveInGetErrorTextW(mmresult, buf, _ERROR_TEXT_LEN) == MMSYSERR_NOERROR:
        return buf.value
    return "unknown error"


def _require_windows() -> None:
    if not _IS_WINDOWS:
        raise WinMMError("platform", "WinMM audio is only available on Windows")


def _configure_prototypes() -> None:
    """Set argtypes/restype for every WinMM/kernel32 entry point used below.

    `DWORD_PTR`/`LPARAM`-shaped callback parameters are bound as `c_void_p`,
    never `wintypes.DWORD`: those are pointer-sized, and a fixed 32-bit
    field would truncate the event handle passed as `dwCallback` on 64-bit
    Windows.
    """

    winmm.waveInOpen.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ]
    winmm.waveInOpen.restype = wintypes.UINT
    winmm.waveInPrepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    winmm.waveInPrepareHeader.restype = wintypes.UINT
    winmm.waveInUnprepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    winmm.waveInUnprepareHeader.restype = wintypes.UINT
    winmm.waveInAddBuffer.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    winmm.waveInAddBuffer.restype = wintypes.UINT
    winmm.waveInStart.argtypes = [wintypes.HANDLE]
    winmm.waveInStart.restype = wintypes.UINT
    winmm.waveInStop.argtypes = [wintypes.HANDLE]
    winmm.waveInStop.restype = wintypes.UINT
    winmm.waveInReset.argtypes = [wintypes.HANDLE]
    winmm.waveInReset.restype = wintypes.UINT
    winmm.waveInClose.argtypes = [wintypes.HANDLE]
    winmm.waveInClose.restype = wintypes.UINT
    winmm.waveInGetErrorTextW.argtypes = [wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    winmm.waveInGetErrorTextW.restype = wintypes.UINT

    winmm.waveOutOpen.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ]
    winmm.waveOutOpen.restype = wintypes.UINT
    winmm.waveOutPrepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    winmm.waveOutPrepareHeader.restype = wintypes.UINT
    winmm.waveOutUnprepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    winmm.waveOutUnprepareHeader.restype = wintypes.UINT
    winmm.waveOutWrite.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    winmm.waveOutWrite.restype = wintypes.UINT
    winmm.waveOutReset.argtypes = [wintypes.HANDLE]
    winmm.waveOutReset.restype = wintypes.UINT
    winmm.waveOutClose.argtypes = [wintypes.HANDLE]
    winmm.waveOutClose.restype = wintypes.UINT

    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


if _IS_WINDOWS:
    _configure_prototypes()


def _pcm_format() -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag = WAVE_FORMAT_PCM
    fmt.nChannels = 1
    fmt.nSamplesPerSec = SAMPLE_RATE_HZ
    fmt.wBitsPerSample = 16
    fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample // 8
    fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign
    fmt.cbSize = 0
    return fmt


def _check(function: str, result: int) -> None:
    if result != MMSYSERR_NOERROR:
        raise WinMMError(function, result)


# Four ~100ms physical buffers in flight: small enough to keep wake-word
# latency low, large enough that the completion event doesn't fire faster
# than a Python thread can service it.
_BUFFER_FRAMES = 4
_BUFFER_BYTES = FRAME_BYTES * _BUFFER_FRAMES
_NUM_BUFFERS = 4


class WinMMMicrophoneSource:
    """Captures real microphone audio via WinMM's `waveIn*` API.

    Implements `AudioSource`. `read_frame()` always returns exactly
    `FRAME_BYTES` (the pinned wire contract), carved out of an internal
    accumulator fed by a background thread: physical WinMM buffers are
    larger (100ms) to keep driver overhead low, decoupled from the 25ms
    frame contract every consumer expects.
    """

    def __init__(self, *, device_id: int = WAVE_MAPPER) -> None:
        _require_windows()
        self._device_id = device_id
        self._hwavein = wintypes.HANDLE()
        self._event: int | None = None
        self._buffers: list[tuple[ctypes.Array, WAVEHDR]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._queue: "queue.Queue[bytes]" = queue.Queue()
        self._accumulator = bytearray()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        fmt = _pcm_format()
        event = kernel32.CreateEventW(None, False, False, None)
        if not event:
            raise WinMMError("CreateEventW", ctypes.get_last_error())
        self._event = event
        _check(
            "waveInOpen",
            winmm.waveInOpen(
                ctypes.byref(self._hwavein), self._device_id, ctypes.byref(fmt),
                ctypes.c_void_p(event), None, CALLBACK_EVENT,
            ),
        )
        self._buffers = []
        for _ in range(_NUM_BUFFERS):
            buf = ctypes.create_string_buffer(_BUFFER_BYTES)
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = _BUFFER_BYTES
            _check("waveInPrepareHeader", winmm.waveInPrepareHeader(self._hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr)))
            _check("waveInAddBuffer", winmm.waveInAddBuffer(self._hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr)))
            self._buffers.append((buf, hdr))
        _check("waveInStart", winmm.waveInStart(self._hwavein))
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True, name="haven-mic-capture")
        self._thread.start()
        self._started = True

    def _pump(self) -> None:
        while not self._stop.is_set():
            waited = kernel32.WaitForSingleObject(self._event, 200)
            if waited == WAIT_TIMEOUT:
                continue
            for buf, hdr in self._buffers:
                if hdr.dwFlags & WHDR_DONE:
                    self._queue.put(bytes(buf.raw[: hdr.dwBytesRecorded]))
                    # Re-queue immediately: the driver clears WHDR_DONE once
                    # it takes ownership again for the next fill cycle.
                    winmm.waveInAddBuffer(self._hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))

    def read_frame(self) -> bytes:
        while len(self._accumulator) < FRAME_BYTES:
            if self._stop.is_set() and self._queue.empty():
                return b""
            try:
                chunk = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._accumulator.extend(chunk)
        frame = bytes(self._accumulator[:FRAME_BYTES])
        del self._accumulator[:FRAME_BYTES]
        return frame

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        winmm.waveInStop(self._hwavein)
        winmm.waveInReset(self._hwavein)
        for _buf, hdr in self._buffers:
            winmm.waveInUnprepareHeader(self._hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInClose(self._hwavein)
        if self._event:
            kernel32.CloseHandle(self._event)
            self._event = None
        self._buffers = []
        self._accumulator.clear()
        self._started = False


class WinMMSpeakerSink:
    """Plays PCM chunks through a real speaker via WinMM's `waveOut*` API.

    Implements `PlaybackSink`. Each `play()` call opens its own device and
    writes chunks synchronously (wait for one buffer's completion before
    writing the next) -- simpler and safer than a buffer pool for what is,
    in practice, one scripted utterance at a time; the tiny inter-chunk
    gap this can introduce is not the barge-in latency this contract cares
    about. `stop()` sets a flag `play()` checks between chunks and, for the
    chunk currently in flight, calls `waveOutReset()` -- documented to mark
    all pending buffers done immediately -- which is what makes truncation
    from another thread possible without any model or LLM call.
    """

    def __init__(self, *, device_id: int = WAVE_MAPPER) -> None:
        _require_windows()
        self._device_id = device_id
        self._stop_requested = threading.Event()

    def play(self, chunks: Iterable[bytes]) -> None:
        self._stop_requested.clear()
        fmt = _pcm_format()
        hwaveout = wintypes.HANDLE()
        event = kernel32.CreateEventW(None, False, False, None)
        if not event:
            raise WinMMError("CreateEventW", ctypes.get_last_error())
        try:
            _check(
                "waveOutOpen",
                winmm.waveOutOpen(
                    ctypes.byref(hwaveout), self._device_id, ctypes.byref(fmt),
                    ctypes.c_void_p(event), None, CALLBACK_EVENT,
                ),
            )
            try:
                for chunk in chunks:
                    if self._stop_requested.is_set():
                        break
                    data = bytes(chunk)
                    if not data:
                        continue
                    self._write_chunk(hwaveout, event, data)
            finally:
                winmm.waveOutReset(hwaveout)
                winmm.waveOutClose(hwaveout)
        finally:
            kernel32.CloseHandle(event)

    def _write_chunk(self, hwaveout, event: int, data: bytes) -> None:
        buf = ctypes.create_string_buffer(data, len(data))
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(data)
        _check("waveOutPrepareHeader", winmm.waveOutPrepareHeader(hwaveout, ctypes.byref(hdr), ctypes.sizeof(hdr)))
        _check("waveOutWrite", winmm.waveOutWrite(hwaveout, ctypes.byref(hdr), ctypes.sizeof(hdr)))
        while not (hdr.dwFlags & WHDR_DONE) and not self._stop_requested.is_set():
            kernel32.WaitForSingleObject(event, 200)
        if not (hdr.dwFlags & WHDR_DONE):
            # Stopped mid-playback: waveOutReset guarantees WHDR_DONE gets
            # set, which is required before unpreparing (otherwise
            # WAVERR_STILLPLAYING).
            winmm.waveOutReset(hwaveout)
        winmm.waveOutUnprepareHeader(hwaveout, ctypes.byref(hdr), ctypes.sizeof(hdr))

    def stop(self) -> None:
        self._stop_requested.set()


__all__ = ["WAVEFORMATEX", "WAVEHDR", "WinMMError", "WinMMMicrophoneSource", "WinMMSpeakerSink"]
