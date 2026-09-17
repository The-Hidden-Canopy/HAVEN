"""`SpeechSession`: the state machine that enforces the speech-layer invariants.

The raw pieces (wake detector, VAD, recognizer, speaker identification) each
answer one narrow question. This session composes them into the one
pipeline the rest of Haven may talk to, and it enforces four invariants
structurally, not by convention:

1. Never execute from a partial -- `TranscriptPartial` is recorded for
   presentation, but the only transcript the session ever hands out is a
   `TranscriptFinal` via `consume_final()`. No API path from a partial to
   an action exists on this object.
2. Pre-roll is prepended -- opening on a `WakeEvent` seeds the utterance
   with the ring buffer, so capture starts at "Haven, turn off..." and
   never at "...turn off...".
3. Raw audio is discarded on `close()` unless `retain_audio` was set. The
   default is the privacy-preserving one: text may remain, PCM may not.
4. A final transcript carries the session's best `SpeakerClaim` from the
   utterance -- attached as a claim, never promoted to authority.

The session is transport-agnostic: feed it audio with `on_audio` and events
with `handle`, from a microphone, a file, or a test script alike.
"""

from __future__ import annotations

from enum import Enum

from .events import (
    SpeakerClaim,
    SpeechContinued,
    SpeechEnded,
    SpeechResponseStarted,
    SpeechResponseStopped,
    SpeechStarted,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)
from .ring_buffer import PrerollRingBuffer


class SpeechSessionState(str, Enum):
    DORMANT = "dormant"
    CAPTURING = "capturing"
    RECOGNIZING = "recognizing"
    HAVEN_INPUT_READY = "haven_input_ready"
    CLOSED = "closed"


class SpeechSession:
    """Turns raw speech events and audio into invariant-enforcing haven input."""

    def __init__(self, *, preroll: PrerollRingBuffer | None = None, retain_audio: bool = False) -> None:
        self._preroll = preroll if preroll is not None else PrerollRingBuffer()
        self._retain_audio = bool(retain_audio)
        self._state = SpeechSessionState.DORMANT
        self._utterance = bytearray()
        self._partials: list[TranscriptPartial] = []
        self._claims: list[SpeakerClaim] = []
        self._pending_final: TranscriptFinal | None = None

    @property
    def state(self) -> SpeechSessionState:
        return self._state

    @property
    def retain_audio(self) -> bool:
        return self._retain_audio

    @property
    def utterance(self) -> bytes:
        """The PCM captured for the current utterance, pre-roll included."""
        return bytes(self._utterance)

    @property
    def partials(self) -> tuple[TranscriptPartial, ...]:
        """Interim hypotheses of the CURRENT utterance, for presentation
        only; never executable. Cleared on wake, on `consume_final()`, and
        on `close()` -- partials never leak across utterances."""
        return tuple(self._partials)

    @property
    def pending_final(self) -> TranscriptFinal | None:
        """The committed transcript awaiting `consume_final()`."""
        return self._pending_final

    def on_audio(self, pcm: bytes) -> None:
        """Route one chunk of PCM: pre-roll while dormant, utterance while capturing."""
        self._require_open()
        if not isinstance(pcm, (bytes, bytearray)):
            raise ValueError("pcm must be bytes")
        if self._state is SpeechSessionState.DORMANT:
            self._preroll.write(pcm)
        elif self._state is SpeechSessionState.CAPTURING:
            self._utterance += pcm

    def handle(self, event: object) -> None:
        """Dispatch one speech event to the matching `on_*` handler."""
        if isinstance(event, WakeEvent):
            self.on_wake(event)
        elif isinstance(event, (SpeechStarted, SpeechContinued, SpeechEnded)):
            self.on_vad(event)
        elif isinstance(event, (TranscriptPartial, TranscriptFinal)):
            self.on_transcript(event)
        elif isinstance(event, SpeakerClaim):
            self.on_speaker_claim(event)
        elif isinstance(event, (SpeechResponseStarted, SpeechResponseStopped)):
            # Playback lifecycle: downstream echo-suppression cares, the
            # capture pipeline does not.
            self._require_open()
        else:
            raise ValueError(f"unsupported speech event: {type(event).__name__}")

    def on_wake(self, event: WakeEvent) -> None:
        """Open capture: prepend the pre-roll so the utterance is whole."""
        self._require_open()
        if not isinstance(event, WakeEvent):
            raise ValueError("event must be a WakeEvent")
        # A wake in any active state restarts the utterance: barge-in wins.
        self._utterance = bytearray(self._preroll.read_preroll())
        self._preroll.clear()
        self._partials = []
        self._claims = []
        self._pending_final = None
        self._state = SpeechSessionState.CAPTURING

    def on_vad(self, event: SpeechStarted | SpeechContinued | SpeechEnded) -> None:
        self._require_open()
        if not isinstance(event, (SpeechStarted, SpeechContinued, SpeechEnded)):
            raise ValueError("event must be a VAD event")
        if self._state is SpeechSessionState.CAPTURING and isinstance(event, SpeechEnded):
            self._state = SpeechSessionState.RECOGNIZING

    def on_transcript(self, event: TranscriptPartial | TranscriptFinal) -> None:
        """Record a partial for presentation, or commit a final as haven input."""
        self._require_open()
        if not isinstance(event, (TranscriptPartial, TranscriptFinal)):
            raise ValueError("event must be a transcript event")
        if self._state is SpeechSessionState.DORMANT:
            return
        if isinstance(event, TranscriptPartial):
            self._partials.append(event)
            return
        if self._state is SpeechSessionState.HAVEN_INPUT_READY:
            raise ValueError("a final transcript is already pending consumption")
        self._pending_final = self._attach_best_claim(event)
        self._state = SpeechSessionState.HAVEN_INPUT_READY

    def on_speaker_claim(self, claim: SpeakerClaim) -> None:
        """Collect a speaker claim as evidence for the current utterance."""
        self._require_open()
        if not isinstance(claim, SpeakerClaim):
            raise ValueError("claim must be a SpeakerClaim")
        if self._state in (SpeechSessionState.CAPTURING, SpeechSessionState.RECOGNIZING):
            self._claims.append(claim)

    def consume_final(self) -> TranscriptFinal | None:
        """Yield the committed transcript -- the only executable output.

        Returns `None` when nothing is committed; after yielding, the
        session returns to dormant and the utterance's raw audio, partials,
        and claims are dropped (unless retained). There is deliberately no
        method that returns, accepts, or acts on a `TranscriptPartial`.
        """
        self._require_open()
        if self._state is not SpeechSessionState.HAVEN_INPUT_READY:
            return None
        final = self._pending_final
        self._pending_final = None
        self._partials = []
        self._claims = []
        if not self._retain_audio:
            self._utterance.clear()
        self._state = SpeechSessionState.DORMANT
        return final

    def close(self) -> None:
        """End the session.

        Raw audio is discarded here by default: `retain_audio=True` is the
        explicit, opt-in exception for diagnostics. Text already committed
        as finals stays; PCM, partials, and interim state do not survive.
        """
        if self._state is SpeechSessionState.CLOSED:
            return
        self._state = SpeechSessionState.CLOSED
        self._preroll.clear()
        if not self._retain_audio:
            self._utterance.clear()
        self._partials = []
        self._claims = []
        self._pending_final = None

    def _attach_best_claim(self, final: TranscriptFinal) -> TranscriptFinal:
        if not self._claims:
            return final
        best = max(self._claims, key=lambda claim: claim.confidence)
        # `dataclasses.replace` is avoided on purpose: constructing the new
        # final keeps validation in exactly one place (the event itself).
        return TranscriptFinal(
            text=final.text,
            start_ms=final.start_ms,
            end_ms=final.end_ms,
            confidence=final.confidence,
            at_ms=final.at_ms,
            speaker_claim=best,
        )

    def _require_open(self) -> None:
        if self._state is SpeechSessionState.CLOSED:
            raise ValueError("the speech session is closed")


__all__ = ["SpeechSession", "SpeechSessionState"]
