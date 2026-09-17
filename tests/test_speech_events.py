import dataclasses

import pytest

from haven.speech import (
    SpeechContinued,
    SpeechEnded,
    SpeechResponseStarted,
    SpeechResponseStopped,
    SpeechStarted,
    SpeakerClaim,
    TranscriptFinal,
    TranscriptPartial,
    WakeEvent,
)


def test_wake_event_defaults_to_haven_phrase():
    event = WakeEvent(confidence=0.8, at_ms=100)

    assert event.phrase == "haven"
    assert event.confidence == 0.8


def test_confidence_is_clamped_into_unit_interval():
    assert WakeEvent(confidence=1.7, at_ms=0).confidence == 1.0
    assert WakeEvent(confidence=-0.5, at_ms=0).confidence == 0.0
    assert TranscriptFinal(
        text="hi", start_ms=0, end_ms=100, confidence=2.0, at_ms=100
    ).confidence == 1.0
    assert SpeakerClaim(candidate_person_id="person.maya", confidence=-1.0, at_ms=0).confidence == 0.0


def test_confidence_must_be_a_number():
    with pytest.raises(ValueError):
        WakeEvent(confidence="high", at_ms=0)
    with pytest.raises(ValueError):
        SpeakerClaim(candidate_person_id="person.maya", confidence=True, at_ms=0)


@pytest.mark.parametrize(
    "make",
    [
        lambda at_ms: WakeEvent(confidence=1.0, at_ms=at_ms),
        lambda at_ms: SpeechStarted(at_ms),
        lambda at_ms: SpeechContinued(at_ms),
        lambda at_ms: SpeechEnded(at_ms, duration_ms=25),
        lambda at_ms: TranscriptPartial(text="hi", at_ms=at_ms),
        lambda at_ms: TranscriptFinal(text="hi", start_ms=at_ms, end_ms=at_ms + 25, confidence=1.0, at_ms=at_ms),
        lambda at_ms: SpeakerClaim(candidate_person_id="person.maya", confidence=1.0, at_ms=at_ms),
        lambda at_ms: SpeechResponseStarted(at_ms),
        lambda at_ms: SpeechResponseStopped(at_ms),
    ],
)
def test_negative_milliseconds_are_rejected(make):
    with pytest.raises(ValueError):
        make(-1)


def test_final_transcript_interval_must_not_run_backwards():
    with pytest.raises(ValueError):
        TranscriptFinal(text="hi", start_ms=200, end_ms=100, confidence=1.0, at_ms=200)


def test_final_transcript_validates_attached_claim():
    with pytest.raises(ValueError):
        TranscriptFinal(
            text="hi",
            start_ms=0,
            end_ms=100,
            confidence=1.0,
            at_ms=100,
            speaker_claim="person.maya",
        )


def test_speech_ended_requires_positive_duration():
    with pytest.raises(ValueError):
        SpeechEnded(at_ms=100, duration_ms=0)


def test_speaker_claim_requires_identity():
    with pytest.raises(ValueError):
        SpeakerClaim(candidate_person_id="   ", confidence=1.0, at_ms=0)


def test_events_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        WakeEvent(confidence=1.0, at_ms=0).confidence = 0.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        TranscriptPartial(text="hi", at_ms=0).text = "bye"


def test_speaker_claim_docstring_declares_it_is_not_identity_truth():
    doc = SpeakerClaim.__doc__

    assert "never identity truth" in doc
    assert "voice match is not permission" in doc


def test_partial_docstring_declares_it_is_presentation_only():
    assert "presentation only" in TranscriptPartial.__doc__
