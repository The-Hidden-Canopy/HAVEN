"""Immutable, scoped domain values for the first HAVEN slice.

These values deliberately contain no network clients or model execution logic.
They describe what was observed, what was proposed, and what was authorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping

from .time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_confidence(value: float, *, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a number between 0.0 and 1.0")


def _normalize_parameters(
    parameters: Mapping[str, Any] | Iterable[tuple[str, Any]],
) -> tuple[tuple[str, Any], ...]:
    items = tuple(parameters.items()) if isinstance(parameters, Mapping) else tuple(parameters)
    normalized = []
    seen: set[str] = set()
    for key, value in items:
        key = _require_text(key, name="parameter name")
        if key in seen:
            raise ValueError(f"duplicate parameter: {key}")
        seen.add(key)
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda item: item[0]))


class RoleTier(IntEnum):
    """Human household role tiers used by authority decisions."""

    GUEST = 10
    MEMBER = 20
    ADMIN = 30
    OWNER = 40


class EvidenceStatus(str, Enum):
    OBSERVED = "observed"
    DECLARED = "declared"
    STALE = "stale"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


class ActionKind(str, Enum):
    TURN_LIGHT_OFF = "turn_light_off"
    SET_LIGHT_BRIGHTNESS = "set_light_brightness"
    ACTIVATE_SCENE = "activate_scene"
    SET_THERMOSTAT = "set_thermostat"
    OPEN_GARAGE = "open_garage"
    CLOSE_GARAGE = "close_garage"
    UNLOCK_DOOR = "unlock_door"
    PURCHASE = "purchase"
    CHANGE_ALARM = "change_alarm"
    UNSCOPED_EXECUTION = "unscoped_execution"


class RiskTier(str, Enum):
    SAFE_AUTOMATIC = "safe_automatic"
    CONDITIONAL = "conditional"
    CONFIRMATION_REQUIRED = "confirmation_required"
    FORBIDDEN = "forbidden"


class DecisionStatus(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRMATION_REQUIRED = "confirmation_required"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNAVAILABLE = "unavailable"


class DecisionCode(str, Enum):
    ALLOWED = "allowed"
    CROSS_HOUSEHOLD = "cross_household"
    ACTOR_MISMATCH = "actor_mismatch"
    WRONG_ROLE_TIER = "wrong_role_tier"
    MISSING_JUSTIFICATION = "missing_justification"
    NEEDS_CLARIFICATION = "needs_clarification"
    RULE_NOT_APPROVED = "rule_not_approved"
    RULE_APPROVAL_INSUFFICIENT = "rule_approval_insufficient"
    RULE_EXPIRED = "rule_expired"
    ACTION_MISMATCH = "action_mismatch"
    CLARIFICATION_MISMATCH = "clarification_mismatch"
    FORBIDDEN_ACTION = "forbidden_action"
    UNKNOWN_CAPABILITY = "unknown_capability"
    HUMAN_OVERRIDE_ACTIVE = "human_override_active"
    STALE_EVIDENCE = "stale_evidence"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    EVIDENCE_MISSING = "evidence_missing"
    LOW_CONFIDENCE_EVIDENCE = "low_confidence_evidence"
    TRIGGER_NOT_ACTIVE = "trigger_not_active"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_REUSED = "confirmation_reused"
    INVALID_STATE_TRANSITION = "invalid_state_transition"


class RuleStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REVOKED = "revoked"


class ActionStatus(str, Enum):
    AUTHORIZED = "authorized"
    EXECUTED = "executed"
    BLOCKED = "blocked"


class TransitionKind(str, Enum):
    PROPOSE_RULE = "propose_rule"
    CLARIFY_RULE = "clarify_rule"
    APPROVE_RULE = "approve_rule"
    AUTHORIZE_ACTION = "authorize_action"
    RECORD_EXECUTION = "record_execution"
    RECORD_BLOCK = "record_block"
    RECORD_RULE_DECISION = "record_rule_decision"


class ChangeOrigin(str, Enum):
    """Who last changed a device's observed state.

    This is deliberately binary rather than an exhaustive actor taxonomy:
    the only thing `AuthorityEngine` needs to decide is whether a household
    member just touched this device directly, which should suspend
    automation for a window, versus everything else (an automation, the
    initial/unknown state), which should not.
    """

    HUMAN = "human"
    SYSTEM = "system"


class EventType(str, Enum):
    RULE_PROPOSED = "rule_proposed"
    RULE_CLARIFIED = "rule_clarified"
    RULE_APPROVED = "rule_approved"
    RULE_APPROVAL_BLOCKED = "rule_approval_blocked"
    RULE_CLARIFICATION_BLOCKED = "rule_clarification_blocked"
    ACTION_AUTHORIZED = "action_authorized"
    ACTION_EXECUTED = "action_executed"
    ACTION_BLOCKED = "action_blocked"


@dataclass(frozen=True)
class Principal:
    actor_id: str
    household_id: str
    role_tier: RoleTier

    def __post_init__(self) -> None:
        _require_text(self.actor_id, name="actor_id")
        _require_text(self.household_id, name="household_id")
        if not isinstance(self.role_tier, RoleTier):
            raise ValueError("role_tier must be a RoleTier")


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    subject_id: str
    status: EvidenceStatus
    observed_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text(self.kind, name="evidence kind")
        _require_text(self.subject_id, name="evidence subject_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("evidence status must be an EvidenceStatus")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        _require_text(self.source, name="evidence source")


@dataclass(frozen=True)
class PresenceState:
    person_id: str
    room_id: str
    present: bool
    observed_at: datetime
    source: str
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _require_text(self.person_id, name="person_id")
        _require_text(self.room_id, name="room_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("presence status must be an EvidenceStatus")
        _require_confidence(self.confidence, name="presence confidence")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        _require_text(self.source, name="presence source")


@dataclass(frozen=True)
class ContextState:
    context_id: str
    active: bool
    observed_at: datetime
    source: str
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _require_text(self.context_id, name="context_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("context status must be an EvidenceStatus")
        _require_confidence(self.confidence, name="context confidence")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        _require_text(self.source, name="context source")


@dataclass(frozen=True)
class DeviceState:
    device_id: str
    kind: str
    room_id: str
    is_on: bool | None
    brightness_pct: int | None
    observed_at: datetime
    source: str
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    changed_by: ChangeOrigin = ChangeOrigin.SYSTEM
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _require_text(self.device_id, name="device_id")
        _require_text(self.kind, name="device kind")
        _require_text(self.room_id, name="device room_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("device status must be an EvidenceStatus")
        if not isinstance(self.changed_by, ChangeOrigin):
            raise ValueError("changed_by must be a ChangeOrigin")
        _require_confidence(self.confidence, name="device confidence")
        if self.brightness_pct is not None and not 0 <= self.brightness_pct <= 100:
            raise ValueError("brightness_pct must be between 0 and 100")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        _require_text(self.source, name="device source")


@dataclass(frozen=True)
class DeviceSelector:
    """A resolvable request for "every device like this", not one device_id.

    A rule normally names one `target_device_id`. A selector lets it instead
    say "every light in the bedroom" and have that resolved against the
    device registry at run time -- so "turn off all the bedroom lights"
    fans out to whichever concrete devices currently match, including a
    switch wired to a lamp, without the rule being rewritten when a device
    is added or renamed.
    """

    role: str | None = None
    room: str | None = None
    device_type: str | None = None
    requires_capability: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("role", "room", "device_type", "requires_capability"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _require_text(value, name=field_name))
        if self.role is None and self.room is None and self.device_type is None and self.requires_capability is None:
            raise ValueError("a device selector must constrain at least one of role, room, device_type, requires_capability")


@dataclass(frozen=True)
class Prediction:
    """A declared, explainable forecast from a prediction provider.

    Nothing in Haven Core produces predictions; this is the contract a
    prediction engine (a world model, Ghost Teacher) plugs into. A
    prediction is not an observation -- it can only authorize an action
    through a rule that explicitly declared a `PredictionTrigger` for it,
    and the receipt records it as DECLARED evidence rather than observed
    fact.
    """

    event: str
    subject_id: str
    confidence: float
    explanation: str
    observed_at: datetime
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event", _require_text(self.event, name="prediction event"))
        object.__setattr__(self, "subject_id", _require_text(self.subject_id, name="prediction subject_id"))
        _require_confidence(self.confidence, name="prediction confidence")
        object.__setattr__(self, "explanation", _require_text(self.explanation, name="prediction explanation"))
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        object.__setattr__(self, "source", _require_text(self.source, name="prediction source"))


@dataclass(frozen=True)
class PredictionTrigger:
    """A rule trigger that fires on a predicted event, not observed presence.

    The rule declares the bar it trusts: a prediction of this event must
    meet `min_confidence` before it can authorize anything. Predictions are
    inherently probabilistic, so they are judged by this declared bar
    rather than by the engine's `minimum_confidence` for observed evidence
    (which defaults to fail-closed at 1.0). `subject_id` optionally pins the
    trigger to predictions about one room or device.
    """

    event: str
    min_confidence: float
    subject_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event", _require_text(self.event, name="prediction trigger event"))
        _require_confidence(self.min_confidence, name="prediction trigger min_confidence")
        if self.subject_id is not None:
            object.__setattr__(self, "subject_id", _require_text(self.subject_id, name="prediction trigger subject_id"))


@dataclass(frozen=True)
class ScheduleTrigger:
    """A rule trigger that fires at a time of day, on given weekdays.

    This answers "is `at` inside this schedule's window right now" -- a
    pure, stateless check, the same shape as every other trigger. It does
    not track whether it already fired today: Haven Core has no scheduler
    daemon (the same way it has no polling loop for `HomeAssistantObserver`),
    so avoiding a duplicate run within one day is a caller's job, not this
    engine's. `time_of_day` and `at` are compared as given -- there is no
    household timezone concept yet, so a caller is responsible for passing
    `at` in whatever wall-clock meaning `time_of_day` was declared against.

    `weekdays` uses `datetime.weekday()`'s convention (Monday=0..Sunday=6);
    an empty set means every day. `window` is how long after `time_of_day`
    the schedule is still considered due, to tolerate a caller that checks
    periodically rather than at the exact instant.
    """

    time_of_day: time
    weekdays: frozenset[int] = frozenset()
    window: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if not isinstance(self.time_of_day, time):
            raise ValueError("time_of_day must be a datetime.time")
        weekdays = frozenset(self.weekdays)
        if any(not isinstance(day, int) or not 0 <= day <= 6 for day in weekdays):
            raise ValueError("weekdays must be integers 0 (Monday) through 6 (Sunday)")
        object.__setattr__(self, "weekdays", weekdays)
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")

    def is_due(self, at: datetime) -> bool:
        at = require_aware_utc(at, name="schedule check time")
        if self.weekdays and at.weekday() not in self.weekdays:
            return False
        today_start = datetime.combine(at.date(), self.time_of_day, tzinfo=at.tzinfo)
        return today_start <= at < today_start + self.window


class IntentForm:
    """Shared base marking a value as one form of natural-language intent.

    The intent union itself lives in `haven.intelligence.intents` (the
    intelligence layer may not be importable from core); this base exists
    here so that `RuleDraft`, a core value, can join the union by
    inheritance rather than by wrapper. An interpreter -- an agent or a
    deterministic parser -- proposes exactly one intent form, and routing
    differs per form: queries are answered, direct actions go straight to
    authority, rule drafts enter the proposal lifecycle.
    """


@dataclass(frozen=True)
class RuleDraft(IntentForm):
    draft_id: str
    household_id: str
    proposed_by: str
    source_text: str
    interpretation: str
    action_kind: ActionKind
    trigger_person_id: str | None = None
    trigger_room_id: str | None = None
    required_context: str | None = None
    prediction_trigger: PredictionTrigger | None = None
    schedule_trigger: ScheduleTrigger | None = None
    target_device_id: str | None = None
    parameters: tuple[tuple[str, Any], ...] = ()
    assumptions: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    capability: str | None = None
    target_selector: DeviceSelector | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "draft_id",
            "household_id",
            "proposed_by",
            "source_text",
            "interpretation",
        ):
            _require_text(getattr(self, field_name), name=field_name)
        if not isinstance(self.action_kind, ActionKind):
            raise ValueError("action_kind must be an ActionKind")
        has_person = self.trigger_person_id is not None
        has_room = self.trigger_room_id is not None
        if has_person != has_room:
            raise ValueError("a presence trigger requires both trigger_person_id and trigger_room_id")
        trigger_kinds = [has_person, self.prediction_trigger is not None, self.schedule_trigger is not None]
        if sum(trigger_kinds) != 1:
            raise ValueError(
                "a rule draft must set exactly one of a presence trigger, a prediction_trigger, or a schedule_trigger"
            )
        if self.trigger_person_id is not None:
            object.__setattr__(self, "trigger_person_id", _require_text(self.trigger_person_id, name="trigger_person_id"))
            object.__setattr__(self, "trigger_room_id", _require_text(self.trigger_room_id, name="trigger_room_id"))
        if self.prediction_trigger is not None and not isinstance(self.prediction_trigger, PredictionTrigger):
            raise ValueError("prediction_trigger must be a PredictionTrigger")
        if self.schedule_trigger is not None and not isinstance(self.schedule_trigger, ScheduleTrigger):
            raise ValueError("schedule_trigger must be a ScheduleTrigger")
        if self.required_context is not None:
            _require_text(self.required_context, name="required_context")
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at, name="expires_at"))
        if self.capability is not None:
            object.__setattr__(self, "capability", _require_text(self.capability, name="capability"))
        if self.target_selector is not None and not isinstance(self.target_selector, DeviceSelector):
            raise ValueError("target_selector must be a DeviceSelector")
        if (self.target_device_id is None) == (self.target_selector is None):
            raise ValueError("a rule draft must set exactly one of target_device_id or target_selector")
        if self.target_device_id is not None:
            object.__setattr__(self, "target_device_id", _require_text(self.target_device_id, name="target_device_id"))
        object.__setattr__(self, "parameters", _normalize_parameters(self.parameters))
        object.__setattr__(self, "assumptions", tuple(_require_text(v, name="assumption") for v in self.assumptions))
        object.__setattr__(self, "unresolved", tuple(_require_text(v, name="unresolved item") for v in self.unresolved))


@dataclass(frozen=True)
class Rule:
    rule_id: str
    draft: RuleDraft
    status: RuleStatus = RuleStatus.PROPOSED
    approved_by: str | None = None
    approved_by_role: RoleTier | None = None
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.rule_id, name="rule_id")
        if not isinstance(self.status, RuleStatus):
            raise ValueError("rule status must be a RuleStatus")
        if self.status == RuleStatus.APPROVED:
            if not self.approved_by or self.approved_by_role != RoleTier.OWNER or self.approved_at is None:
                raise ValueError("an approved rule requires owner approval and a timestamp")
        if self.approved_at is not None:
            object.__setattr__(self, "approved_at", require_aware_utc(self.approved_at, name="approved_at"))


@dataclass(frozen=True)
class WorldSnapshot:
    snapshot_id: str
    household_id: str
    captured_at: datetime
    valid_until: datetime
    presence: tuple[PresenceState, ...] = ()
    contexts: tuple[ContextState, ...] = ()
    devices: tuple[DeviceState, ...] = ()
    predictions: tuple[Prediction, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.snapshot_id, name="snapshot_id")
        _require_text(self.household_id, name="household_id")
        captured_at = require_aware_utc(self.captured_at, name="captured_at")
        valid_until = require_aware_utc(self.valid_until, name="valid_until")
        if valid_until <= captured_at:
            raise ValueError("valid_until must be later than captured_at")
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "valid_until", valid_until)
        object.__setattr__(self, "presence", tuple(self.presence))
        object.__setattr__(self, "contexts", tuple(self.contexts))
        object.__setattr__(self, "devices", tuple(self.devices))
        object.__setattr__(self, "predictions", tuple(self.predictions))
        for state in (*self.presence, *self.contexts, *self.devices):
            if not isinstance(state, (PresenceState, ContextState, DeviceState)):
                raise ValueError("world snapshot collections contain an invalid state value")
            if state.status == EvidenceStatus.DECLARED:
                continue
            if state.observed_at > valid_until:
                raise ValueError("evidence cannot be observed after snapshot validity")
        for prediction in self.predictions:
            if not isinstance(prediction, Prediction):
                raise ValueError("world snapshot predictions contain an invalid value")
            if prediction.observed_at > valid_until:
                raise ValueError("a prediction cannot be made after snapshot validity")

    def _fresh_observation(self, *, status: EvidenceStatus, observed_at: datetime, at: datetime) -> bool:
        at = require_aware_utc(at, name="decision time")
        return (
            self.captured_at <= at <= self.valid_until
            and status == EvidenceStatus.OBSERVED
            and observed_at <= at
        )

    def evidence_problem(
        self, *, status: EvidenceStatus, observed_at: datetime, confidence: float, at: datetime, minimum_confidence: float
    ) -> DecisionCode | None:
        at = require_aware_utc(at, name="decision time")
        if status == EvidenceStatus.UNAVAILABLE:
            return DecisionCode.EVIDENCE_UNAVAILABLE
        if confidence < minimum_confidence:
            return DecisionCode.LOW_CONFIDENCE_EVIDENCE
        if not self._fresh_observation(status=status, observed_at=observed_at, at=at):
            return DecisionCode.STALE_EVIDENCE
        return None

    def _matching_prediction(self, trigger: PredictionTrigger, *, at: datetime) -> Prediction | None:
        candidates = [
            prediction
            for prediction in self.predictions
            if prediction.event == trigger.event
            and (trigger.subject_id is None or prediction.subject_id == trigger.subject_id)
            and prediction.observed_at <= at
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda prediction: prediction.observed_at)

    def _prediction_problem(self, trigger: PredictionTrigger, *, at: datetime) -> DecisionCode | None:
        """Judge a prediction trigger against its own declared min_confidence.

        This is deliberately separate from `evidence_problem`: a prediction
        is judged against the bar the rule itself declared when it was
        approved, not against `AuthorityEngine.minimum_confidence` (which
        governs observed evidence and defaults to fail-closed at 1.0). A
        household approves a prediction-triggered rule precisely because it
        has already decided what confidence bar that specific rule should
        run at.
        """

        prediction = self._matching_prediction(trigger, at=at)
        if prediction is None:
            return DecisionCode.EVIDENCE_MISSING
        if prediction.confidence < trigger.min_confidence:
            return DecisionCode.LOW_CONFIDENCE_EVIDENCE
        return None

    def prediction_evidence(self, trigger: PredictionTrigger, *, at: datetime) -> Prediction | None:
        return self._matching_prediction(trigger, at=at)

    def device_for(self, device_id: str) -> DeviceState | None:
        return next((item for item in self.devices if item.device_id == device_id), None)

    def presence_for(self, person_id: str, room_id: str) -> PresenceState | None:
        return next(
            (item for item in self.presence if item.person_id == person_id and item.room_id == room_id), None
        )

    def context_for(self, context_id: str) -> ContextState | None:
        return next((item for item in self.contexts if item.context_id == context_id), None)

    def evaluate_trigger(
        self,
        draft: RuleDraft,
        *,
        at: datetime,
        target_device_id: str,
        minimum_confidence: float = 0.0,
    ) -> tuple[bool, DecisionCode]:
        """Evaluate the rule trigger without treating missing evidence as false.

        `target_device_id` is passed explicitly rather than read from
        `draft.target_device_id`, since a selector-based draft has no single
        device of its own -- the caller resolves the selector to a concrete
        device first and evaluates the trigger once per resolved device.

        `minimum_confidence` defaults to 0.0 (accept anything): a probabilistic
        observation -- from a vision or IR provider, say -- carries a
        `confidence` below 1.0, and a caller that wants to require a higher
        bar before trusting it (e.g. `AuthorityEngine.minimum_confidence`)
        passes that threshold in explicitly rather than this method assuming
        one.
        """

        at = require_aware_utc(at, name="decision time")

        if draft.prediction_trigger is not None:
            issue = self._prediction_problem(draft.prediction_trigger, at=at)
            if issue is not None:
                return False, issue
        elif draft.schedule_trigger is not None:
            if not draft.schedule_trigger.is_due(at):
                return False, DecisionCode.TRIGGER_NOT_ACTIVE
        else:
            presence = self.presence_for(draft.trigger_person_id, draft.trigger_room_id)
            if presence is None:
                return False, DecisionCode.EVIDENCE_MISSING
            issue = self.evidence_problem(
                status=presence.status,
                observed_at=presence.observed_at,
                confidence=presence.confidence,
                at=at,
                minimum_confidence=minimum_confidence,
            )
            if issue is not None:
                return False, issue
            if not presence.present:
                return False, DecisionCode.TRIGGER_NOT_ACTIVE

        # required_context is a condition layered on top of any trigger kind
        # -- "every weekday at 6:30, but not on vacation" is a schedule
        # trigger plus a context condition, not a fourth trigger kind.
        if draft.required_context is not None:
            context = self.context_for(draft.required_context)
            if context is None:
                return False, DecisionCode.EVIDENCE_MISSING
            issue = self.evidence_problem(
                status=context.status,
                observed_at=context.observed_at,
                confidence=context.confidence,
                at=at,
                minimum_confidence=minimum_confidence,
            )
            if issue is not None:
                return False, issue
            if not context.active:
                return False, DecisionCode.TRIGGER_NOT_ACTIVE

        device = self.device_for(target_device_id)
        if device is None:
            return False, DecisionCode.EVIDENCE_MISSING
        issue = self.evidence_problem(
            status=device.status,
            observed_at=device.observed_at,
            confidence=device.confidence,
            at=at,
            minimum_confidence=minimum_confidence,
        )
        if issue is not None:
            return False, issue
        return True, DecisionCode.ALLOWED

    def evidence_for_rule(
        self, draft: RuleDraft, *, target_device_id: str, at: datetime | None = None
    ) -> tuple[EvidenceRef, ...]:
        refs: list[EvidenceRef] = []
        if draft.prediction_trigger is not None:
            prediction = self._matching_prediction(draft.prediction_trigger, at=at or self.valid_until)
            if prediction is not None:
                refs.append(
                    EvidenceRef(
                        kind="prediction",
                        subject_id=f"{prediction.event}:{prediction.subject_id}",
                        status=EvidenceStatus.DECLARED,
                        observed_at=prediction.observed_at,
                        source=prediction.source,
                    )
                )
        presence = (
            self.presence_for(draft.trigger_person_id, draft.trigger_room_id)
            if draft.trigger_person_id is not None
            else None
        )
        if presence is not None:
            refs.append(
                EvidenceRef(
                    kind="presence",
                    subject_id=f"{presence.person_id}:{presence.room_id}",
                    status=presence.status,
                    observed_at=presence.observed_at,
                    source=presence.source,
                )
            )
        if draft.required_context is not None:
            context = self.context_for(draft.required_context)
            if context is not None:
                refs.append(
                    EvidenceRef(
                        kind="context",
                        subject_id=context.context_id,
                        status=context.status,
                        observed_at=context.observed_at,
                        source=context.source,
                    )
                )
        device = self.device_for(target_device_id)
        if device is not None:
            refs.append(
                EvidenceRef(
                    kind="device",
                    subject_id=device.device_id,
                    status=device.status,
                    observed_at=device.observed_at,
                    source=device.source,
                )
            )
        return tuple(refs)


@dataclass(frozen=True)
class ConfirmationToken:
    """A single-use confirmation grant bound to one proposed action."""

    token_id: str
    household_id: str
    rule_id: str
    request_id: str
    confirmed_by: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("token_id", "household_id", "rule_id", "request_id", "confirmed_by"):
            _require_text(getattr(self, field_name), name=field_name)
        issued_at = require_aware_utc(self.issued_at, name="confirmation issued_at")
        expires_at = require_aware_utc(self.expires_at, name="confirmation expires_at")
        if expires_at <= issued_at:
            raise ValueError("confirmation expires_at must be later than issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)

    def is_valid_for(
        self,
        *,
        household_id: str,
        rule_id: str,
        request_id: str,
        confirmed_by: str,
        at: datetime,
    ) -> bool:
        at = require_aware_utc(at, name="confirmation check time")
        return (
            self.household_id == household_id
            and self.rule_id == rule_id
            and self.request_id == request_id
            and self.confirmed_by == confirmed_by
            and self.issued_at <= at <= self.expires_at
        )


# A direct (human-initiated, one-shot) action names no rule, but
# `ActionRequest.rule_id` must be non-empty. `HavenRuntime.run_action` uses
# this prefix to build a sentinel id of the form "direct-<request_id>":
# honest about the request's origin, bound to the request it authorized, and
# guaranteed to collide with no stored rule id. The store's AUTHORIZE_ACTION
# reducer recognizes the prefix and skips the approved-rule gate for it.
DIRECT_ACTION_RULE_ID_PREFIX = "direct-"


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    household_id: str
    requested_by: str
    rule_id: str
    action_kind: ActionKind
    target_device_id: str
    parameters: tuple[tuple[str, Any], ...]
    justification: str
    evidence_snapshot_id: str
    requested_at: datetime
    confirmation_token: ConfirmationToken | None = None
    capability: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "request_id",
            "household_id",
            "requested_by",
            "rule_id",
            "target_device_id",
            "evidence_snapshot_id",
        ):
            _require_text(getattr(self, field_name), name=field_name)
        if not isinstance(self.action_kind, ActionKind):
            raise ValueError("action_kind must be an ActionKind")
        if not isinstance(self.justification, str):
            raise ValueError("justification must be a string")
        if self.confirmation_token is not None and not isinstance(self.confirmation_token, ConfirmationToken):
            raise ValueError("confirmation_token must be a ConfirmationToken")
        if self.capability is not None:
            object.__setattr__(self, "capability", _require_text(self.capability, name="capability"))
        object.__setattr__(self, "parameters", _normalize_parameters(self.parameters))
        object.__setattr__(self, "requested_at", require_aware_utc(self.requested_at, name="requested_at"))


@dataclass(frozen=True)
class AuthorityDecision:
    status: DecisionStatus
    code: DecisionCode
    explanation: str
    required_role: RoleTier | None = None

    def __post_init__(self) -> None:
        _require_text(self.explanation, name="decision explanation")
        if not isinstance(self.status, DecisionStatus):
            raise ValueError("decision status must be a DecisionStatus")
        if not isinstance(self.code, DecisionCode):
            raise ValueError("decision code must be a DecisionCode")


@dataclass(frozen=True)
class DeviceCommand:
    request_id: str
    target_device_id: str
    service: str
    parameters: tuple[tuple[str, Any], ...]
    requested_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.request_id, name="command request_id")
        _require_text(self.target_device_id, name="command target_device_id")
        _require_text(self.service, name="command service")
        object.__setattr__(self, "parameters", _normalize_parameters(self.parameters))
        object.__setattr__(self, "requested_at", require_aware_utc(self.requested_at, name="requested_at"))


@dataclass(frozen=True)
class DeviceResult:
    success: bool
    detail: str
    observed_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text(self.detail, name="device result detail")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="result observed_at"))
        _require_text(self.source, name="device result source")


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    request: ActionRequest
    status: ActionStatus
    decision: AuthorityDecision
    executed_at: datetime | None = None
    result: DeviceResult | None = None

    def __post_init__(self) -> None:
        _require_text(self.action_id, name="action_id")
        if not isinstance(self.status, ActionStatus):
            raise ValueError("action status must be an ActionStatus")
        if not isinstance(self.decision, AuthorityDecision):
            raise ValueError("action decision must be an AuthorityDecision")
        if self.result is not None and not isinstance(self.result, DeviceResult):
            raise ValueError("action result must be a DeviceResult")
        if self.executed_at is not None:
            object.__setattr__(self, "executed_at", require_aware_utc(self.executed_at, name="executed_at"))
        if self.status == ActionStatus.EXECUTED and self.executed_at is None:
            raise ValueError("an executed action requires executed_at")


@dataclass(frozen=True)
class MemoryEntry:
    entry_id: str
    household_id: str
    kind: str
    content: str
    source_event_id: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("entry_id", "household_id", "kind", "content", "source_event_id"):
            _require_text(getattr(self, field_name), name=field_name)
        object.__setattr__(self, "recorded_at", require_aware_utc(self.recorded_at, name="recorded_at"))


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    household_id: str
    event_type: EventType
    actor_id: str
    occurred_at: datetime
    payload: tuple[tuple[str, Any], ...]
    correlation_id: str
    source: str

    def __post_init__(self) -> None:
        for field_name in ("event_id", "household_id", "actor_id", "correlation_id", "source"):
            _require_text(getattr(self, field_name), name=field_name)
        if not isinstance(self.event_type, EventType):
            raise ValueError("event_type must be an EventType")
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at, name="occurred_at"))
        object.__setattr__(self, "payload", _normalize_parameters(self.payload))


@dataclass(frozen=True)
class Transition:
    kind: TransitionKind
    household_id: str
    actor_id: str
    payload: Any
    correlation_id: str

    def __post_init__(self) -> None:
        _require_text(self.household_id, name="transition household_id")
        _require_text(self.actor_id, name="transition actor_id")
        _require_text(self.correlation_id, name="transition correlation_id")
        if not isinstance(self.kind, TransitionKind):
            raise ValueError("transition kind must be a TransitionKind")
