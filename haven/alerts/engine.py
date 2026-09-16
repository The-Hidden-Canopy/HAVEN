"""AlertEngine: evaluate one AlertRule against one WorldSnapshot.

This mirrors `AuthorityEngine.decide()` in spirit -- deterministic policy,
not model judgment, decides whether an alert fires -- but it is read-only.
Evaluating an alert never touches a device, a rule, or the store; it only
ever produces (or does not produce) an `Alert` value for a caller to hand to
a notification channel.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from haven.core.domain import EvidenceRef, EvidenceStatus, WorldSnapshot
from haven.core.time import require_aware_utc

from .models import Alert, AlertRule


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class AlertEngine:
    """Combines presence, context, and prediction evidence into one alert."""

    def evaluate(self, rule: AlertRule, world: WorldSnapshot, *, at: datetime) -> Alert | None:
        """Return an Alert if every condition on `rule` holds against `world` at `at`.

        Every condition must hold -- this is AND, not OR, by design: an
        `AlertRule` correlates signals ("person detected AND household away")
        rather than firing on any one of them. `None` means no match; it is
        not an error, since most (rule, snapshot) evaluations will not fire.
        """

        if rule.household_id != world.household_id:
            raise ValueError("an alert rule can only be evaluated against its own household's world snapshot")
        at = require_aware_utc(at, name="evaluation time")

        evidence: list[EvidenceRef] = []

        for condition in rule.presence_conditions:
            state = world.presence_for(condition.person_id, condition.room_id)
            if state is None:
                return None
            if world.evidence_problem(
                status=state.status,
                observed_at=state.observed_at,
                confidence=state.confidence,
                at=at,
                minimum_confidence=rule.minimum_confidence,
            ):
                return None
            if state.present != condition.present:
                return None
            evidence.append(
                EvidenceRef(
                    kind="presence",
                    subject_id=f"{state.person_id}:{state.room_id}",
                    status=state.status,
                    observed_at=state.observed_at,
                    source=state.source,
                )
            )

        for condition in rule.context_conditions:
            state = world.context_for(condition.context_id)
            if state is None:
                return None
            if world.evidence_problem(
                status=state.status,
                observed_at=state.observed_at,
                confidence=state.confidence,
                at=at,
                minimum_confidence=rule.minimum_confidence,
            ):
                return None
            if state.active != condition.active:
                return None
            evidence.append(
                EvidenceRef(
                    kind="context",
                    subject_id=state.context_id,
                    status=state.status,
                    observed_at=state.observed_at,
                    source=state.source,
                )
            )

        if rule.prediction_trigger is not None:
            prediction = world.prediction_evidence(rule.prediction_trigger, at=at)
            if prediction is None or prediction.confidence < rule.prediction_trigger.min_confidence:
                return None
            evidence.append(
                EvidenceRef(
                    kind="prediction",
                    subject_id=f"{prediction.event}:{prediction.subject_id}",
                    status=EvidenceStatus.DECLARED,
                    observed_at=prediction.observed_at,
                    source=prediction.source,
                )
            )

        return Alert(
            alert_id=_new_id("alert"),
            household_id=rule.household_id,
            severity=rule.severity,
            event_kind=rule.event_kind,
            summary=rule.summary,
            triggered_at=at,
            evidence=tuple(evidence),
        )


__all__ = ["AlertEngine"]
