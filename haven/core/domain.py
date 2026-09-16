"""Immutable, scoped domain values for the first HAVEN slice.

These values deliberately contain no network clients or model execution logic.
They describe what was observed, what was proposed, and what was authorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping

from .time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


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
    ACTION_MISMATCH = "action_mismatch"
    CLARIFICATION_MISMATCH = "clarification_mismatch"
    FORBIDDEN_ACTION = "forbidden_action"
    STALE_EVIDENCE = "stale_evidence"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    EVIDENCE_MISSING = "evidence_missing"
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

    def __post_init__(self) -> None:
        _require_text(self.person_id, name="person_id")
        _require_text(self.room_id, name="room_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("presence status must be an EvidenceStatus")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        _require_text(self.source, name="presence source")


@dataclass(frozen=True)
class ContextState:
    context_id: str
    active: bool
    observed_at: datetime
    source: str
    status: EvidenceStatus = EvidenceStatus.OBSERVED

    def __post_init__(self) -> None:
        _require_text(self.context_id, name="context_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("context status must be an EvidenceStatus")
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

    def __post_init__(self) -> None:
        _require_text(self.device_id, name="device_id")
        _require_text(self.kind, name="device kind")
        _require_text(self.room_id, name="device room_id")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("device status must be an EvidenceStatus")
        if self.brightness_pct is not None and not 0 <= self.brightness_pct <= 100:
            raise ValueError("brightness_pct must be between 0 and 100")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="observed_at"))
        _require_text(self.source, name="device source")


@dataclass(frozen=True)
class RuleDraft:
    draft_id: str
    household_id: str
    proposed_by: str
    source_text: str
    interpretation: str
    trigger_person_id: str
    trigger_room_id: str
    required_context: str | None
    action_kind: ActionKind
    target_device_id: str
    parameters: tuple[tuple[str, Any], ...] = ()
    assumptions: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "draft_id",
            "household_id",
            "proposed_by",
            "source_text",
            "interpretation",
            "trigger_person_id",
            "trigger_room_id",
            "target_device_id",
        ):
            _require_text(getattr(self, field_name), name=field_name)
        if not isinstance(self.action_kind, ActionKind):
            raise ValueError("action_kind must be an ActionKind")
        if self.required_context is not None:
            _require_text(self.required_context, name="required_context")
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
        for state in (*self.presence, *self.contexts, *self.devices):
            if not isinstance(state, (PresenceState, ContextState, DeviceState)):
                raise ValueError("world snapshot collections contain an invalid state value")
            if state.status == EvidenceStatus.DECLARED:
                continue
            if state.observed_at > valid_until:
                raise ValueError("evidence cannot be observed after snapshot validity")

    def _fresh_observation(self, *, status: EvidenceStatus, observed_at: datetime, at: datetime) -> bool:
        at = require_aware_utc(at, name="decision time")
        return (
            self.captured_at <= at <= self.valid_until
            and status == EvidenceStatus.OBSERVED
            and observed_at <= at
        )

    def _evidence_problem(self, *, status: EvidenceStatus, observed_at: datetime, at: datetime) -> DecisionCode | None:
        at = require_aware_utc(at, name="decision time")
        if status == EvidenceStatus.UNAVAILABLE:
            return DecisionCode.EVIDENCE_UNAVAILABLE
        if not self._fresh_observation(status=status, observed_at=observed_at, at=at):
            return DecisionCode.STALE_EVIDENCE
        return None

    def evaluate_trigger(self, draft: RuleDraft, *, at: datetime) -> tuple[bool, DecisionCode]:
        """Evaluate the rule trigger without treating missing evidence as false."""

        at = require_aware_utc(at, name="decision time")
        presence = next(
            (
                item
                for item in self.presence
                if item.person_id == draft.trigger_person_id and item.room_id == draft.trigger_room_id
            ),
            None,
        )
        if presence is None:
            return False, DecisionCode.EVIDENCE_MISSING
        issue = self._evidence_problem(status=presence.status, observed_at=presence.observed_at, at=at)
        if issue is not None:
            return False, issue
        if not presence.present:
            return False, DecisionCode.TRIGGER_NOT_ACTIVE

        if draft.required_context is not None:
            context = next((item for item in self.contexts if item.context_id == draft.required_context), None)
            if context is None:
                return False, DecisionCode.EVIDENCE_MISSING
            issue = self._evidence_problem(status=context.status, observed_at=context.observed_at, at=at)
            if issue is not None:
                return False, issue
            if not context.active:
                return False, DecisionCode.TRIGGER_NOT_ACTIVE

        device = next((item for item in self.devices if item.device_id == draft.target_device_id), None)
        if device is None:
            return False, DecisionCode.EVIDENCE_MISSING
        issue = self._evidence_problem(status=device.status, observed_at=device.observed_at, at=at)
        if issue is not None:
            return False, issue
        return True, DecisionCode.ALLOWED

    def evidence_for_rule(self, draft: RuleDraft) -> tuple[EvidenceRef, ...]:
        refs: list[EvidenceRef] = []
        presence = next(
            (
                item
                for item in self.presence
                if item.person_id == draft.trigger_person_id and item.room_id == draft.trigger_room_id
            ),
            None,
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
            context = next((item for item in self.contexts if item.context_id == draft.required_context), None)
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
        device = next((item for item in self.devices if item.device_id == draft.target_device_id), None)
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
