import pytest

from haven.speech import (
    FRAME_BYTES,
    PrerollRingBuffer,
    ScriptedAsrProvider,
    ScriptedSynthesizer,
    ScriptedVad,
    ScriptedWakeDetector,
    SpeakerClaim,
    SpeechEnded,
    SpeechSession,
    SpeechSessionState,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)


def _frame(fill: int) -> bytes:
    return bytes((fill,)) * FRAME_BYTES


def _drive_session(session: SpeechSession, frames: list[bytes], *providers) -> None:
    """Feed audio and provider events into the session, frame by frame."""
    for frame in frames:
        for provider in providers:
            for event in provider.process(frame):
                session.handle(event)
        session.on_audio(frame)


def test_wake_prepends_preroll_so_the_utterance_is_whole():
    preroll = PrerollRingBuffer(capacity_ms=1000)
    session = SpeechSession(preroll=preroll)
    pre_roll_audio = _frame(0x11)
    late_audio = _frame(0x22)

    session.on_audio(pre_roll_audio)  # dormant: buffered as pre-roll
    assert session.utterance == b""

    session.handle(WakeEvent(confidence=0.9, at_ms=200))
    assert session.state is SpeechSessionState.CAPTURING

    session.on_audio(late_audio)

    utterance = session.utterance
    assert utterance.startswith(pre_roll_audio)
    assert utterance == pre_roll_audio + late_audio


def test_partials_are_recorded_but_never_executable_output():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))

    partial = TranscriptPartial(text="turn", at_ms=100)
    session.handle(partial)

    assert session.partials == (partial,)
    assert session.state is SpeechSessionState.CAPTURING
    assert session.consume_final() is None
    assert session.pending_final is None


def test_partials_are_scoped_to_one_utterance():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    first = TranscriptPartial(text="turn", at_ms=50)
    session.handle(first)
    session.handle(
        TranscriptFinal(text="turn off the lights", start_ms=0, end_ms=100, confidence=0.9, at_ms=100)
    )
    session.consume_final()
    assert session.partials == ()

    # Utterance B begins; only its own partials may be visible.
    session.handle(WakeEvent(confidence=0.9, at_ms=200))
    second = TranscriptPartial(text="dim", at_ms=250)
    session.handle(second)

    assert session.partials == (second,)


def test_wake_restarts_clear_partials_for_barge_in():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    session.handle(TranscriptPartial(text="turn", at_ms=50))

    # A second wake mid-utterance restarts it: barge-in discards the old partials.
    session.handle(WakeEvent(confidence=0.9, at_ms=100))

    assert session.partials == ()


def test_close_clears_partials():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    session.handle(TranscriptPartial(text="turn", at_ms=50))

    session.close()

    assert session.state is SpeechSessionState.CLOSED
    assert session.partials == ()


def test_full_pipeline_wake_capture_recognize_consume():
    wake = ScriptedWakeDetector(triggers_at_frame=1)
    vad = ScriptedVad(script=((1, 3),))
    asr = ScriptedAsrProvider(script=("turn", "off", "turn off the bedroom lights"))
    session = SpeechSession()
    frames = [_frame(i % 251) for i in range(8)]

    _drive_session(session, frames, wake, vad)
    assert session.state is SpeechSessionState.RECOGNIZING
    utterance = session.utterance
    assert len(utterance) == 4 * FRAME_BYTES  # wake frame + three speech frames, pre-roll empty

    while True:
        results = asr.accept(utterance)
        if not results:
            break
        for result in results:
            session.handle(result)
    finals = asr.finalize()
    assert len(finals) == 1
    session.handle(finals[0])

    assert session.state is SpeechSessionState.HAVEN_INPUT_READY
    assert [p.text for p in session.partials] == ["turn", "off"]
    final = session.consume_final()

    assert final.text == "turn off the bedroom lights"
    assert isinstance(final, TranscriptFinal)
    # Consumed: the session returns to dormant, and the utterance's
    # partials are dropped along with everything else interim.
    assert session.partials == ()
    assert session.state is SpeechSessionState.DORMANT
    assert session.consume_final() is None
    assert session.pending_final is None


def test_best_speaker_claim_rides_on_the_final_as_a_claim():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    session.on_audio(_frame(1))

    session.handle(SpeakerClaim(candidate_person_id="person.unknown", confidence=0.4, at_ms=25))
    session.handle(SpeakerClaim(candidate_person_id="person.maya", confidence=0.9, at_ms=50))
    session.handle(SpeechEnded(at_ms=100, duration_ms=75))
    session.handle(
        TranscriptFinal(text="turn off the lights", start_ms=0, end_ms=100, confidence=0.95, at_ms=100)
    )

    final = session.consume_final()
    assert final.speaker_claim is not None
    assert final.speaker_claim.candidate_person_id == "person.maya"
    assert final.speaker_claim.confidence == 0.9
    # The claim is evidence on the transcript, not a promotion: the session
    # holds no actor/identity state of its own.
    assert session.pending_final is None


def test_claims_are_scoped_to_one_utterance():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    session.handle(SpeakerClaim(candidate_person_id="person.maya", confidence=0.9, at_ms=25))
    session.handle(TranscriptFinal(text="first", start_ms=0, end_ms=50, confidence=0.9, at_ms=50))
    session.consume_final()

    session.handle(WakeEvent(confidence=0.9, at_ms=100))
    session.handle(TranscriptFinal(text="second", start_ms=100, end_ms=150, confidence=0.9, at_ms=150))

    assert session.consume_final().speaker_claim is None


def test_close_discards_raw_audio_by_default():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    session.on_audio(_frame(1))

    session.close()

    assert session.state is SpeechSessionState.CLOSED
    assert session.utterance == b""
    with pytest.raises(ValueError):
        session.on_audio(_frame(2))
    with pytest.raises(ValueError):
        session.handle(WakeEvent(confidence=0.9, at_ms=300))


def test_retain_audio_is_an_explicit_opt_in():
    session = SpeechSession(retain_audio=True)
    session.handle(WakeEvent(confidence=0.9, at_ms=0))
    audio = _frame(1)
    session.on_audio(audio)

    session.close()

    assert session.utterance == audio


def test_second_final_before_consumption_is_rejected():
    session = SpeechSession()
    session.handle(WakeEvent(confidence=0.9, at_ms=0))

    session.handle(TranscriptFinal(text="one", start_ms=0, end_ms=50, confidence=0.9, at_ms=50))
    with pytest.raises(ValueError):
        session.handle(TranscriptFinal(text="two", start_ms=50, end_ms=100, confidence=0.9, at_ms=100))


def test_unknown_events_are_rejected():
    session = SpeechSession()

    with pytest.raises(ValueError):
        session.handle(object())


def test_synthesizer_stop_truncates_playback_without_a_model_call():
    synth = ScriptedSynthesizer(chunk_count=8)

    full = list(synth.speak("hello"))
    assert len(full) == 9  # priming chunk + 8 audio chunks
    assert full[0] == b""

    stream = synth.speak("hello")
    assert next(stream) == b""  # priming
    synth.stop()
    assert list(stream) == []  # nothing further may play after stop


def test_synthesizer_stop_mid_utterance_truncates_the_tail():
    synth = ScriptedSynthesizer(chunk_count=8)

    stream = synth.speak("hello")
    next(stream)
    next(stream)
    next(stream)  # priming + two audio chunks
    synth.stop()

    assert len(list(stream)) == 0


def test_synthesizer_speak_requires_text():
    synth = ScriptedSynthesizer()

    with pytest.raises(ValueError):
        list(synth.speak("  "))
