"""AlertEngine: "person at front door AND nobody expected AND household
away -> high-priority alert" is the shape this proves. Every declared
condition must hold (AND, not OR) -- that's the correlation that turns raw
detections into a small number of alerts worth surfacing, instead of one
notification per detector.
"""

from datetime import timedelta

import pytest

from haven.alerts import Alert, AlertEngine, AlertRule, AlertSeverity, ContextCondition, PresenceCondition
from haven.core.domain import ContextState, EvidenceStatus, PredictionTrigger, PresenceState, WorldSnapshot
from test_vertical_slice import BASE_TIME

HOUSEHOLD = "household-a"


def _world(*, presence=(), contexts=(), predictions=(), household_id: str = HOUSEHOLD) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="alert-snapshot",
        household_id=household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=presence,
        contexts=contexts,
        predictions=predictions,
    )


def _front_door_rule(**overrides) -> AlertRule:
    defaults = dict(
        alert_id="alert-front-door",
        household_id=HOUSEHOLD,
        event_kind="unexpected_visitor",
        severity=AlertSeverity.URGENT,
        summary="Someone is at the front door and nobody is expected while the household is away.",
        presence_conditions=(PresenceCondition(person_id="unidentified", room_id="front_door", present=True),),
        context_conditions=(ContextCondition(context_id="household_away", active=True),),
    )
    defaults.update(overrides)
    return AlertRule(**defaults)


def test_all_conditions_matching_fires_the_alert():
    world = _world(
        presence=(
            PresenceState(
                person_id="unidentified", room_id="front_door", present=True, observed_at=BASE_TIME, source="cam"
            ),
        ),
        contexts=(
            ContextState(context_id="household_away", active=True, observed_at=BASE_TIME, source="fixture.context"),
        ),
    )

    alert = AlertEngine().evaluate(_front_door_rule(), world, at=BASE_TIME + timedelta(seconds=5))

    assert isinstance(alert, Alert)
    assert alert.severity == AlertSeverity.URGENT
    assert alert.event_kind == "unexpected_visitor"
    kinds = {e.kind for e in alert.evidence}
    assert kinds == {"presence", "context"}


def test_recognized_household_member_does_not_fire():
    """Person crossed driveway BUT recognized household member -> no alert."""
    world = _world(
        presence=(
            PresenceState(
                person_id="unidentified", room_id="front_door", present=False, observed_at=BASE_TIME, source="cam"
            ),
        ),
        contexts=(
            ContextState(context_id="household_away", active=True, observed_at=BASE_TIME, source="fixture.context"),
        ),
    )

    assert AlertEngine().evaluate(_front_door_rule(), world, at=BASE_TIME) is None


def test_household_home_does_not_fire_even_with_a_visitor():
    world = _world(
        presence=(
            PresenceState(
                person_id="unidentified", room_id="front_door", present=True, observed_at=BASE_TIME, source="cam"
            ),
        ),
        contexts=(
            ContextState(context_id="household_away", active=False, observed_at=BASE_TIME, source="fixture.context"),
        ),
    )

    assert AlertEngine().evaluate(_front_door_rule(), world, at=BASE_TIME) is None


def test_missing_condition_evidence_does_not_fire():
    world = _world(
        presence=(
            PresenceState(
                person_id="unidentified", room_id="front_door", present=True, observed_at=BASE_TIME, source="cam"
            ),
        ),
        contexts=(),  # household_away has no reading at all
    )

    assert AlertEngine().evaluate(_front_door_rule(), world, at=BASE_TIME) is None


def test_low_confidence_detection_does_not_fire():
    world = _world(
        presence=(
            PresenceState(
                person_id="unidentified",
                room_id="front_door",
                present=True,
                observed_at=BASE_TIME,
                source="cam",
                confidence=0.7,
            ),
        ),
        contexts=(
            ContextState(context_id="household_away", active=True, observed_at=BASE_TIME, source="fixture.context"),
        ),
    )

    rule = _front_door_rule(minimum_confidence=1.0)
    assert AlertEngine().evaluate(rule, world, at=BASE_TIME) is None

    lenient = _front_door_rule(minimum_confidence=0.5)
    assert AlertEngine().evaluate(lenient, world, at=BASE_TIME) is not None


def test_prediction_condition_participates_in_the_same_correlation():
    rule = AlertRule(
        alert_id="alert-package",
        household_id=HOUSEHOLD,
        event_kind="package_delivered",
        severity=AlertSeverity.NOTICE,
        summary="A package was likely delivered.",
        prediction_trigger=PredictionTrigger(event="package_delivered", min_confidence=0.8, subject_id="front_door"),
    )
    from haven.core.domain import Prediction

    world = _world(
        predictions=(
            Prediction(
                event="package_delivered",
                subject_id="front_door",
                confidence=0.9,
                explanation="Delivery vehicle departed and a new object appeared at the door.",
                observed_at=BASE_TIME,
                source="ghost_teacher.world_model",
            ),
        )
    )

    alert = AlertEngine().evaluate(rule, world, at=BASE_TIME + timedelta(seconds=1))
    assert alert is not None
    assert alert.evidence[0].status == EvidenceStatus.DECLARED


def test_cross_household_evaluation_is_rejected():
    world = _world(household_id="household-b")
    with pytest.raises(ValueError):
        AlertEngine().evaluate(_front_door_rule(), world, at=BASE_TIME)


def test_a_rule_with_no_conditions_is_rejected_at_construction():
    with pytest.raises(ValueError):
        AlertRule(
            alert_id="alert-empty",
            household_id=HOUSEHOLD,
            event_kind="x",
            severity=AlertSeverity.INFO,
            summary="This would fire unconditionally.",
        )
