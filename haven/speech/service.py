"""`SpeechService`: the always-on runtime that owns the audio pipeline.

Doctrine: the browser displays speech state; this service owns the always-on
runtime. It is the single pump between an `AudioSource` and the speech
state machine: source frames flow to the wake detector (always-on mode) and
the `SpeechSession`, and -- while the session captures an utterance -- to
the VAD and the recognizer. Speech-ended triggers recognition, the
committed `TranscriptFinal` reaches `on_utterance`, and `say` drives the
synthesizer into the playback sink. Models arrive via role-assigned loaded
handles (see `shims.py`), never bundled; every role is optional and the
service degrades honestly around whatever is absent.

Two modes, chosen by whether a wake detector is wired:

- Always-on: every frame feeds the wake detector; a `WakeEvent` opens the
  session, which prepends its pre-roll so the utterance is whole.
- Push-to-talk: no wake detector; `begin_utterance()` opens the session
  explicitly.

The state vocabulary the UI displays -- dormant, wake, listening,
interpreting, speaking -- is emitted through `on_speech_state` on every
transition. The pump itself has no wall-clock dependencies: VAD hold
timings are sample-driven, so a synchronous `pump_frame` loop in a test
behaves exactly like the daemon-thread `run()` loop.

Hard requirements honored here:

- Barge-in: `barge_in()` / `stop_speaking()` truncate the sink and the
  synthesizer immediately, with no model or LLM call.
- Honest degradation: an utterance that completes with no recognizer wired
  raises `NoSpeechModelError` (the session still closes cleanly); TTS is
  optional -- `say` with no synthesizer or sink is a documented no-op.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .events import (
    SpeechEnded,
    SpeechResponseStarted,
    SpeechResponseStopped,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)
from .protocols import SpeechRecognizer, SpeechSynthesizer, Vad, WakeDetector
from .session import SpeechSession, SpeechSessionState
from .sinks import PlaybackSink
from .sources import AudioSource
from .vad import EnergyVad

# The UI-facing states, emitted via on_speech_state.
STATE_DORMANT = "dormant"
STATE_WAKE = "wake"
STATE_LISTENING = "listening"
STATE_INTERPRETING = "interpreting"
STATE_SPEAKING = "speaking"


class NoSpeechModelError(RuntimeError):
    """An utterance completed but no recognizer is wired to transcribe it.

    Raised from the pump at the speech-ended boundary; the session still
    closes cleanly and the service remains stoppable. TTS being optional
    is documented doctrine, but an utterance nobody can transcribe is a
    wiring error worth surfacing, not silent swallow.
    """


class SpeechService:
    """Pumps PCM from a source through wake/VAD/session/recognizer to intent."""

    def __init__(
        self,
        *,
        source: AudioSource,
        session: SpeechSession | None = None,
        wake: WakeDetector | None = None,
        vad: Vad | None = None,
        recognizer: SpeechRecognizer | None = None,
        synthesizer: SpeechSynthesizer | None = None,
        sink: PlaybackSink | None = None,
        on_utterance: Callable[[TranscriptFinal], None] | None = None,
        on_speech_state: Callable[[str], None] | None = None,
    ) -> None:
        if source is None:
            raise ValueError("source must be an AudioSource")
        if session is not None and not isinstance(session, SpeechSession):
            raise ValueError("session must be a SpeechSession")
        self._source = source
        self._session = session if session is not None else SpeechSession()
        self._wake = wake
        self._vad = vad if vad is not None else EnergyVad()
        self._recognizer = recognizer
        self._synthesizer = synthesizer
        self._sink = sink
        self._on_utterance = on_utterance
        self._on_speech_state = on_speech_state
        self._state = STATE_DORMANT
        self._thread: threading.Thread | None = None
        self._stop_requested = False
        self._closed = False
        self._pump_error: Exception | None = None

    @property
    def session(self) -> SpeechSession:
        return self._session

    @property
    def state(self) -> str:
        """The current UI-facing state (dormant|wake|listening|interpreting|speaking)."""
        return self._state

    @property
    def pump_error(self) -> Exception | None:
        """The exception that ended the background pump, if any."""
        return self._pump_error

    def run(self) -> None:
        """Pump frames from the source until it is exhausted or stopped.

        Safe to call directly (it blocks until the stream ends); `start`
        is the same loop on a daemon thread.
        """
        self._require_open()
        self._source.start()
        try:
            while not self._stop_requested:
                frame = self._source.read_frame()
                if not frame:
                    break
                try:
                    self.pump_frame(frame)
                except Exception as exc:  # a dead pipeline must not strand the thread
                    self._pump_error = exc
                    break
        finally:
            self._source.stop()

    def start(self) -> threading.Thread:
        """Run the pump loop on a daemon thread; returns the thread."""
        self._require_open()
        if self._thread is not None and self._thread.is_alive():
            raise ValueError("the speech service is already running")
        self._thread = threading.Thread(target=self.run, name="haven-speech-pump", daemon=True)
        self._thread.start()
        return self._thread

    def pump_frame(self, frame: bytes) -> None:
        """Drive one frame through the pipeline; public for synchronous tests."""
        self._require_open()
        if not isinstance(frame, (bytes, bytearray)) or not frame:
            raise ValueError("frame must be non-empty bytes")
        pcm = bytes(frame)
        if self._wake is not None:
            for event in self._wake.process(pcm):
                self._on_wake_event(event)
        self._session.on_audio(pcm)
        if self._session.state is SpeechSessionState.CAPTURING:
            self._pump_capturing(pcm)

    def begin_utterance(self) -> None:
        """Open capture explicitly: the push-to-talk path when no wake detector."""
        self._require_open()
        self._on_wake_event(WakeEvent(confidence=1.0, at_ms=0, phrase="push-to-talk"))

    def say(self, text: str) -> None:
        """Speak `text` through the synthesizer into the sink.

        TTS is optional: with no synthesizer or no sink this is a
        documented no-op, so a pipeline without a TTS model runs
        unchanged.
        """
        self._require_open()
        if self._synthesizer is None or self._sink is None:
            return
        self._set_state(STATE_SPEAKING)
        self._session.handle(SpeechResponseStarted(at_ms=0))
        try:
            self._sink.play(self._synthesizer.speak(text))
        finally:
            self._session.handle(SpeechResponseStopped(at_ms=0))
            self._sync_state()

    def barge_in(self) -> None:
        """Silence the speaker immediately: no model call, within one chunk."""
        self.stop_speaking()

    def stop_speaking(self) -> None:
        """Truncate in-flight playback immediately; a capturing session is untouched."""
        if self._sink is not None:
            self._sink.stop()
        if self._synthesizer is not None:
            self._synthesizer.stop()
        self._sync_state()

    def stop(self) -> None:
        """Cooperatively stop: source, sink, session, pump thread.

        Idempotent; safe to call from a callback running on the pump
        thread itself.
        """
        if self._closed:
            return
        self._closed = True
        self._stop_requested = True
        try:
            self._source.stop()
        except Exception:
            pass
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._session.close()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def close(self) -> None:
        """Alias for `stop`; the service is single-use after this."""
        self.stop()

    # -- internals ---------------------------------------------------------

    def _pump_capturing(self, pcm: bytes) -> None:
        if self._recognizer is not None:
            # Streaming hypotheses are presentation-only; the commit path
            # is finalize() at speech end. A final from accept is ignored
            # so the session never double-commits.
            for event in self._recognizer.accept(pcm):
                if isinstance(event, TranscriptPartial):
                    self._session.on_transcript(event)
        for event in self._vad.process(pcm):
            self._session.on_vad(event)
            if isinstance(event, SpeechEnded):
                self._finish_utterance()

    def _finish_utterance(self) -> None:
        self._set_state(STATE_INTERPRETING)
        recognizer = self._recognizer
        if recognizer is None:
            raise NoSpeechModelError(
                "an utterance completed but no speech recognizer is wired; "
                "assign and load an asr model or pass a recognizer"
            )
        finals = recognizer.finalize()
        for final in finals:
            self._session.on_transcript(final)
        consumed = self._session.consume_final()
        if consumed is not None and self._on_utterance is not None:
            self._on_utterance(consumed)
        self._sync_state()

    def _on_wake_event(self, event: WakeEvent) -> None:
        self._set_state(STATE_WAKE)
        self._session.on_wake(event)
        self._set_state(STATE_LISTENING)

    def _sync_state(self) -> None:
        """Derive the UI state from the session's state machine."""
        session_state = self._session.state
        if session_state is SpeechSessionState.CAPTURING:
            self._set_state(STATE_LISTENING)
        elif session_state in (SpeechSessionState.RECOGNIZING, SpeechSessionState.HAVEN_INPUT_READY):
            self._set_state(STATE_INTERPRETING)
        else:
            self._set_state(STATE_DORMANT)

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if self._on_speech_state is not None:
            self._on_speech_state(state)

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("the speech service is closed")


__all__ = [
    "NoSpeechModelError",
    "SpeechService",
    "STATE_DORMANT",
    "STATE_INTERPRETING",
    "STATE_LISTENING",
    "STATE_SPEAKING",
    "STATE_WAKE",
]
