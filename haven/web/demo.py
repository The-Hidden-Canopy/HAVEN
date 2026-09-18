"""Simulated household and demo director behind the local web surface.

This is the only place the web layer talks to: the director owns a
``HavenRuntime`` wired to a ``SimulatedHouse`` through the real
authority/execution path, interprets a handful of chat intents, keeps the
pending-confirmation bookkeeping the runtime cannot see, and publishes
``(kind, payload)`` events to subscribers (``server.py`` turns those into
SSE). Glow is derived only from engine outcomes.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ChangeOrigin,
    ConfirmationToken,
    ContextState,
    DecisionStatus,
    DeviceState,
    DeviceResult,
    EvidenceStatus,
    PresenceState,
    Principal,
    RoleTier,
    Rule,
    RuleDraft,
    RuleStatus,
    ScheduleTrigger,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.execution import ExecutionProviderRegistry
from haven.intelligence.gateway import AgentContext, ScriptedIntelligenceProvider, UnsupportedIntent
from haven.intelligence.intents import (
    ActionProposal,
    ClarificationRequest,
    QueryRequest,
)
from haven.intelligence.worldview import WorldView
from haven.models import ModelManager
from haven.models.bridge import ModelIntelligenceProvider
from haven.providers import CapabilityRegistry, build_default_registry
from haven.runtime import HavenRuntime
from haven.scheduler import SchedulerEngine

from . import serialize

HOUSEHOLD_ID = "household-demo"
PERSON_ID = "gerron"
PROVIDER_ID = "simulated_house"

GLOW_IDLE = "idle"
GLOW_ACTING = "acting"
GLOW_PERMISSION = "permission"
GLOW_CRITICAL = "critical"
GLOW_COMPLETED = "completed"

ATTENTION_LINE = "HAVEN needs your attention"
NOMINAL_LINE = "Everything nominal"
CAMERA_BLIND_LINE = "HAVEN can't see clearly"

ACTIVITY_LIMIT = 20
MEMORY_LIMIT = 20

DRIVEWAY_CAM_ID = "driveway_cam"
DRIVEWAY_ROOM = "driveway"

# Sentinel for `_set_glow`: "keep the current target" vs. an explicit None.
_KEEP_TARGET: Any = object()

Clock = Callable[[], datetime]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _rule_recency_key(rule: Rule) -> tuple[int, float]:
    # Approved rules first, newest approval first; the rest keep store order.
    if rule.approved_at is not None:
        return (0, -rule.approved_at.timestamp())
    return (1, 0.0)


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    rule_id: str
    title: str
    detail: str
    expires_at: datetime


VOICE_DORMANT = "dormant"
VOICE_WAKE = "wake"
VOICE_LISTENING = "listening"
VOICE_INTERPRETING = "interpreting"

# Capture runs only once the wake acknowledgement has finished, so the mic is
# live in listening/interpreting but not during the brief wake phase.
_MIC_STATES = (VOICE_LISTENING, VOICE_INTERPRETING)

VOICE_ACK_SECONDS = 0.8
VOICE_REFRACTORY_SECONDS = 3.0

STOPPED_LINE = "Stopped."
ALREADY_EXECUTING_LINE = "That action is already executing."

# Utterances that mean "Haven, stop" rather than a chat intent.
STOP_UTTERANCES = ("haven, stop", "stop")

def _is_stop_utterance(text: str) -> bool:
    normalized = " ".join(text.casefold().split()).rstrip(".!?")
    return normalized in STOP_UTTERANCES


class VoiceSession:
    """Wake-word → utterance state machine owned by the ``DemoDirector``.

    Dormant → wake (brief ack, then a ``threading.Timer`` advances to
    listening) → interpreting while the text runs through the same intent path
    as typed chat → dormant, where wake requests are refused for a refractory
    cooldown. Every transition publishes state through the director so SSE
    clients follow along. Timers and the refractory clock are real-time;
    ``expire_ack``/``expire_refractory`` let tests advance them without
    sleeping.
    """

    def __init__(
        self,
        director: DemoDirector,
        *,
        ack_seconds: float = VOICE_ACK_SECONDS,
        refractory_seconds: float = VOICE_REFRACTORY_SECONDS,
    ) -> None:
        self._director = director
        self._ack_seconds = ack_seconds
        self._refractory_seconds = refractory_seconds
        self._state = VOICE_DORMANT
        self._timer: threading.Timer | None = None
        self._refractory_until: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    @property
    def mic_active(self) -> bool:
        return self._state in _MIC_STATES

    def to_dict(self) -> dict[str, Any]:
        return serialize.voice_to_dict(state=self._state, mic=self.mic_active)

    def wake(self) -> bool:
        with self._lock:
            if self._state != VOICE_DORMANT or self._in_refractory():
                return False
            self._refractory_until = None
            self._enter(VOICE_WAKE)
            self._timer = threading.Timer(self._ack_seconds, self._ack_expired)
            self._timer.daemon = True
            self._timer.start()
        return True

    def utterance(self, text: str) -> bool:
        with self._lock:
            if self._state != VOICE_LISTENING:
                return False
            self._enter(VOICE_INTERPRETING)
        self._director._say("user", text)
        if _is_stop_utterance(text):
            # "Haven, stop" said out loud carries the same semantics as the
            # cancel endpoint: deny a pending request, never recall an action.
            self._resolve_stop()
        else:
            # Voice and keyboard are equal input methods: the text flows
            # through the exact chat() intent path.
            self._director._chat_intent(text, focus=self._director.voice_focus())
        with self._lock:
            self._enter(VOICE_DORMANT)
            self._start_refractory()
        return True

    def cancel(self) -> bool:
        with self._lock:
            if self._state not in (VOICE_LISTENING, VOICE_INTERPRETING):
                return False
            self._clear_timer()
            self._enter(VOICE_DORMANT)
            self._start_refractory()
        self._resolve_stop()
        return True

    def _resolve_stop(self) -> None:
        # "Haven, stop": a pending permission request is a deny; an executing
        # action is not recalled, only acknowledged honestly.
        if self._director.pending_requests:
            for pending in self._director.pending_requests:
                self._director.deny(pending.request_id)
        elif self._director._glow == GLOW_ACTING:
            self._director._say("haven", ALREADY_EXECUTING_LINE)
            self._director._publish_state()
        else:
            self._director._say("haven", STOPPED_LINE)
            self._director._publish_state()

    def reset(self) -> None:
        with self._lock:
            self._clear_timer()
            self._state = VOICE_DORMANT
            self._refractory_until = None

    def expire_ack(self) -> None:
        """Test hook: end the wake acknowledgement window immediately."""
        timer = self._timer
        if timer is not None:
            timer.cancel()
        self._ack_expired()

    def expire_refractory(self) -> None:
        """Test hook: clear the post-interaction wake cooldown."""
        with self._lock:
            self._refractory_until = None

    def _ack_expired(self) -> None:
        with self._lock:
            self._timer = None
            if self._state == VOICE_WAKE:
                self._enter(VOICE_LISTENING)

    def _enter(self, state: str) -> None:
        self._state = state
        self._director._publish_state()

    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _start_refractory(self) -> None:
        self._refractory_until = time.monotonic() + self._refractory_seconds

    def _in_refractory(self) -> bool:
        return self._refractory_until is not None and time.monotonic() < self._refractory_until


class SimulatedHouse:
    """Mutable device state for the demo household."""

    GARAGE_OPEN_MINUTES = 18

    def __init__(self, *, now: datetime) -> None:
        self._snapshot_counter = 0
        self.reset(now=now)

    def reset(self, *, now: datetime) -> None:
        opened_at = now - timedelta(minutes=self.GARAGE_OPEN_MINUTES)
        recent = now - timedelta(minutes=1)
        self._camera_down = False
        self._devices: dict[str, dict[str, Any]] = {
            "office_light": {
                "kind": "light",
                "room_id": "office",
                "is_on": True,
                "brightness_pct": 70,
                "observed_at": recent,
                "changed_by": ChangeOrigin.SYSTEM,
            },
            "kitchen_thermostat": {
                "kind": "thermostat",
                "room_id": "kitchen",
                "is_on": True,
                "brightness_pct": None,
                "temperature_f": 72,
                "observed_at": recent,
                "changed_by": ChangeOrigin.SYSTEM,
            },
            "bedroom_desk_fan": {
                "kind": "fan",
                "room_id": "bedroom",
                "is_on": False,
                "brightness_pct": None,
                "observed_at": recent,
                "changed_by": ChangeOrigin.SYSTEM,
            },
            "garage_door": {
                "kind": "cover",
                "room_id": "garage",
                "is_on": True,
                "brightness_pct": None,
                "open": True,
                "observed_at": opened_at,
                "changed_by": ChangeOrigin.SYSTEM,
            },
            "driveway_cam": {
                "kind": "camera",
                "room_id": "driveway",
                "is_on": True,
                "brightness_pct": None,
                "motion": False,
                "observed_at": recent,
                "changed_by": ChangeOrigin.SYSTEM,
            },
        }

    def garage_open(self) -> bool:
        return bool(self._devices["garage_door"]["open"])

    def garage_open_minutes(self, *, at: datetime) -> int:
        return int((at - self._devices["garage_door"]["observed_at"]).total_seconds() // 60)

    def motion_detected(self) -> bool:
        return bool(self._devices["driveway_cam"]["motion"])

    def camera_down(self) -> bool:
        return self._camera_down

    def set_camera_down(self, down: bool) -> None:
        self._camera_down = bool(down)

    def snapshot(self, now: datetime) -> WorldSnapshot:
        self._snapshot_counter += 1
        recent = now - timedelta(minutes=1)
        devices = tuple(
            DeviceState(
                device_id=device_id,
                kind=state["kind"],
                room_id=state["room_id"],
                is_on=state["is_on"],
                brightness_pct=state["brightness_pct"],
                observed_at=state["observed_at"],
                source="demo.house",
                changed_by=state["changed_by"],
                status=(
                    EvidenceStatus.UNAVAILABLE
                    if device_id == DRIVEWAY_CAM_ID and self._camera_down
                    else EvidenceStatus.OBSERVED
                ),
            )
            for device_id, state in self._devices.items()
        )
        return WorldSnapshot(
            snapshot_id=f"demo-snapshot-{self._snapshot_counter}",
            household_id=HOUSEHOLD_ID,
            captured_at=now,
            valid_until=now + timedelta(seconds=30),
            presence=(
                PresenceState(
                    person_id=PERSON_ID,
                    room_id="office",
                    present=True,
                    observed_at=recent,
                    source="demo.presence",
                ),
            ),
            contexts=(
                ContextState(context_id="working_late", active=True, observed_at=recent, source="demo.context"),
                ContextState(context_id="vacation_mode", active=False, observed_at=recent, source="demo.context"),
            ),
            devices=devices,
        )

    def apply(self, command) -> DeviceResult:
        device = self._devices.get(command.target_device_id)
        if device is None:
            return DeviceResult(
                success=False,
                detail=f"unknown device {command.target_device_id}",
                observed_at=command.requested_at,
                source="demo.house",
            )
        if command.service == "cover.close":
            if device["kind"] != "cover":
                return DeviceResult(False, "cover.close on a non-cover device", command.requested_at, "demo.house")
            device["open"] = False
            device["is_on"] = False
            device["observed_at"] = command.requested_at
            device["changed_by"] = ChangeOrigin.SYSTEM
            return DeviceResult(True, "Garage door closed.", command.requested_at, "demo.house")
        if command.service == "light.turn_off":
            device["is_on"] = False
            device["observed_at"] = command.requested_at
            device["changed_by"] = ChangeOrigin.SYSTEM
            return DeviceResult(True, "Light turned off.", command.requested_at, "demo.house")
        if command.service == "camera.record_clip":
            if device["kind"] != "camera":
                return DeviceResult(
                    False, "camera.record_clip on a non-camera device", command.requested_at, "demo.house"
                )
            device["observed_at"] = command.requested_at
            device["changed_by"] = ChangeOrigin.SYSTEM
            return DeviceResult(True, "Driveway clip recorded.", command.requested_at, "demo.house")
        return DeviceResult(
            success=False,
            detail=f"unsupported service {command.service}",
            observed_at=command.requested_at,
            source="demo.house",
        )


class SimulatedExecutionAdapter:
    """Applies authorized commands to the house; never raises."""

    def __init__(self, house: SimulatedHouse) -> None:
        self.house = house
        self.commands: list = []

    def execute(self, command) -> DeviceResult:
        self.commands.append(command)
        return self.house.apply(command)


def build_registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="office_light",
            device_type="light",
            provider_id=PROVIDER_ID,
            room="office",
            semantic_role="light",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
                CapabilityDescriptor(
                    "brightness", ControlClass.MEDIUM, writable=True, service="light.set_brightness"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="kitchen_thermostat",
            device_type="thermostat",
            provider_id=PROVIDER_ID,
            room="kitchen",
            capabilities=(
                CapabilityDescriptor(
                    "temperature", ControlClass.MEDIUM, writable=True, service="climate.set_temperature"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="bedroom_desk_fan",
            device_type="fan",
            provider_id=PROVIDER_ID,
            room="bedroom",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="fan.turn_off"),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="garage_door",
            device_type="cover",
            provider_id=PROVIDER_ID,
            room="garage",
            capabilities=(
                CapabilityDescriptor("close", ControlClass.GUARDED, writable=True, service="cover.close"),
                CapabilityDescriptor("open", ControlClass.GUARDED, writable=True, service="cover.open"),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="driveway_cam",
            device_type="camera",
            provider_id=PROVIDER_ID,
            room="driveway",
            capabilities=(
                CapabilityDescriptor("motion", ControlClass.READ, readable=True),
                CapabilityDescriptor("record_clip", ControlClass.LOW_RISK, writable=True, service="camera.record_clip"),
            ),
        )
    )
    return registry


class DemoDirector:
    """Owns the runtime, house, and demo flows; publishes state/glow events."""

    CONTEXT_LABELS = {"working_late": "Working late", "vacation_mode": "Vacation mode"}

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        voice_ack_seconds: float = VOICE_ACK_SECONDS,
        voice_refractory_seconds: float = VOICE_REFRACTORY_SECONDS,
        capability_registry: CapabilityRegistry | None = None,
        model_manager: ModelManager | None = None,
        scheduler_tick_seconds: float = 20.0,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        now = self._clock()
        self.house = SimulatedHouse(now=now)
        self.registry = build_registry()
        self.adapter = SimulatedExecutionAdapter(self.house)
        providers = ExecutionProviderRegistry()
        providers.register(PROVIDER_ID, self.adapter)
        self.store = HavenStore(household_id=HOUSEHOLD_ID)
        self.capability_registry = capability_registry or build_default_registry()
        self.engine = AuthorityEngine(device_registry=self.registry)
        # The bridge makes models an upgrade path in front of the scripted
        # floor: with no model loaded every chat falls through to the
        # deterministic demo behavior, exactly as before.
        self.model_manager = model_manager if model_manager is not None else ModelManager()
        self.runtime = HavenRuntime(
            store=self.store,
            intelligence_provider=ModelIntelligenceProvider(
                ScriptedIntelligenceProvider(), self.model_manager
            ),
            authority=self.engine,
            execution_providers=providers,
        )
        self.resident = Principal(actor_id=PERSON_ID, household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER)
        self.owner = Principal(actor_id=PERSON_ID, household_id=HOUSEHOLD_ID, role_tier=RoleTier.OWNER)
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._glow = GLOW_IDLE
        self._glow_target: str | None = None
        self._conversation: list[dict[str, Any]] = []
        self._pending: dict[str, PendingRequest] = {}
        self._direct_actions: dict[str, ActionProposal] = {}
        self._auto_allow: set[str] = set()
        self.receipts: list = []
        self._scheduler_tick_seconds = scheduler_tick_seconds
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_stop: threading.Event | None = None
        self.voice = VoiceSession(
            self,
            ack_seconds=voice_ack_seconds,
            refractory_seconds=voice_refractory_seconds,
        )
        self.garage_rule_id = self._prepare_garage_rule(now)
        self.camera_rule_id = self._prepare_camera_rule(now)
        # The scheduler is another requester over the same runtime, asking as
        # the resident. It is in-memory only: the demo has no data dir, so
        # the enabled set lives and dies with the director. The garage and
        # camera pre-approved rules carry 24h schedule windows purely as an
        # "evaluates as due whenever the manual flows invoke them"
        # affordance -- they are not schedules, so the scheduler stays off
        # them; the office-light rule below is the one real schedule.
        self.scheduler = SchedulerEngine(runtime=self.runtime, principal=self.resident)
        self.scheduler.set_enabled(self.garage_rule_id, False)
        self.scheduler.set_enabled(self.camera_rule_id, False)
        self.office_light_rule_id = self._prepare_office_light_rule(now)
        self.start_scenario()

    @property
    def pending_requests(self) -> tuple[PendingRequest, ...]:
        return tuple(self._pending.values())

    def _prepare_garage_rule(self, now: datetime) -> str:
        draft = RuleDraft(
            draft_id="draft-garage-close",
            household_id=HOUSEHOLD_ID,
            proposed_by=self.resident.actor_id,
            source_text="close the garage",
            interpretation=(
                "When the garage door has been open a while with no nearby motion, "
                "ask to close it."
            ),
            action_kind=ActionKind.CLOSE_GARAGE,
            schedule_trigger=ScheduleTrigger(
                time_of_day=now.time().replace(microsecond=0), window=timedelta(hours=24)
            ),
            target_device_id="garage_door",
            capability="close",
        )
        rule = self.runtime.propose_draft(draft, principal=self.resident, now=now)
        self.runtime.approve_rule(
            rule.rule_id,
            principal=self.owner,
            justification="Owner approved the garage-close rule for the demo household.",
            now=now,
        )
        return rule.rule_id

    def _prepare_camera_rule(self, now: datetime) -> str:
        # The rule's trigger evaluation reads the target device's own evidence
        # (WorldSnapshot.evaluate_trigger ends with an evidence_problem check on
        # the target device), so a driveway camera marked UNAVAILABLE makes
        # AuthorityEngine.decide() return UNAVAILABLE through the real path.
        draft = RuleDraft(
            draft_id="draft-driveway-clip",
            household_id=HOUSEHOLD_ID,
            proposed_by=self.resident.actor_id,
            source_text="record a driveway clip",
            interpretation=(
                "Record a short clip on the driveway camera when asked; the rule "
                "only runs while the camera's own evidence is current."
            ),
            action_kind=ActionKind.ACTIVATE_SCENE,
            schedule_trigger=ScheduleTrigger(
                time_of_day=now.time().replace(microsecond=0), window=timedelta(hours=24)
            ),
            target_device_id=DRIVEWAY_CAM_ID,
            capability="record_clip",
        )
        rule = self.runtime.propose_draft(draft, principal=self.resident, now=now)
        self.runtime.approve_rule(
            rule.rule_id,
            principal=self.owner,
            justification="Owner approved the driveway-clip rule for the demo household.",
            now=now,
        )
        return rule.rule_id

    OFFICE_LIGHT_OFF_TIME = dt_time(22, 35)

    def _prepare_office_light_rule(self, now: datetime) -> str:
        # The demo's one real schedule: a SAFE_AUTOMATIC light-off at 22:35
        # every day, observable (the office light state flips) and harmless.
        draft = RuleDraft(
            draft_id="draft-office-light-off",
            household_id=HOUSEHOLD_ID,
            proposed_by=self.resident.actor_id,
            source_text="turn off the office light at 22:35 every day",
            interpretation="Turn off the office light at 22:35 every night.",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            schedule_trigger=ScheduleTrigger(
                time_of_day=self.OFFICE_LIGHT_OFF_TIME, window=timedelta(minutes=10)
            ),
            target_device_id="office_light",
            capability="power",
        )
        rule = self.runtime.propose_draft(draft, principal=self.resident, now=now)
        self.runtime.approve_rule(
            rule.rule_id,
            principal=self.owner,
            justification="Owner approved the nightly office light schedule for the demo household.",
            now=now,
        )
        return rule.rule_id

    def start_scenario(self) -> None:
        now = self._clock()
        self.house.reset(now=now)
        open_minutes = self.house.garage_open_minutes(at=now)
        if (
            not self.house.garage_open()
            or open_minutes < SimulatedHouse.GARAGE_OPEN_MINUTES
            or self.house.motion_detected()
        ):
            self._publish_state()
            return
        receipt = self.runtime.run_rule(
            self.garage_rule_id,
            principal=self.resident,
            world=self.house.snapshot(now),
            justification="Garage has been open 18 minutes with no nearby motion.",
            now=now,
        )
        self.receipts.append(receipt)
        if receipt.decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._ask_garage_close(now, open_minutes)
        elif receipt.decision.status == DecisionStatus.ALLOW:
            self._set_glow(GLOW_COMPLETED)
        else:
            self._record_block(receipt)
        self._publish_state()

    def _ask_garage_close(self, now: datetime, open_minutes: int) -> None:
        pending = PendingRequest(
            request_id=_new_id("request"),
            rule_id=self.garage_rule_id,
            title="Close the garage door?",
            detail=f"It has been open {open_minutes} minutes. No motion detected nearby.",
            expires_at=now + timedelta(minutes=5),
        )
        self._pending[pending.request_id] = pending
        self._say("haven", f"Garage has been open {open_minutes} minutes with no nearby motion.")
        self._say("haven", "Want me to close it?")
        self._set_glow(GLOW_PERMISSION, target=self._room_for_rule(pending.rule_id))
        if pending.rule_id in self._auto_allow:
            self.approve(pending.request_id)

    def approve(self, request_id: str, *, auto: bool = False) -> dict[str, Any] | None:
        pending = self._pending.get(request_id)
        if pending is None:
            return None
        if request_id in self._direct_actions:
            return self._approve_direct_action(request_id, pending)
        if auto:
            self._auto_allow.add(pending.rule_id)
        del self._pending[request_id]
        now = self._clock()
        token = ConfirmationToken(
            token_id=_new_id("confirm"),
            household_id=HOUSEHOLD_ID,
            rule_id=pending.rule_id,
            request_id=request_id,
            confirmed_by=self.resident.actor_id,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._set_glow(GLOW_ACTING, target=self._room_for_rule(pending.rule_id))
        receipt = self.runtime.run_rule(
            pending.rule_id,
            principal=self.resident,
            world=self.house.snapshot(now),
            justification="Owner approved the pending request in the demo UI.",
            now=now,
            confirmation_token=token,
        )
        self.receipts.append(receipt)
        if receipt.decision.status == DecisionStatus.ALLOW:
            self._say("haven", "Done — the garage door is closed.")
            self._set_glow(GLOW_COMPLETED)
        elif receipt.decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._pending[request_id] = pending
            self._set_glow(GLOW_PERMISSION, target=self._room_for_rule(pending.rule_id))
        else:
            self._record_block(receipt)
        self._publish_state()
        return self.state()

    def _approve_direct_action(self, request_id: str, pending: PendingRequest) -> dict[str, Any] | None:
        proposal = self._direct_actions.pop(request_id)
        del self._pending[request_id]
        now = self._clock()
        token = ConfirmationToken(
            token_id=_new_id("confirm"),
            household_id=HOUSEHOLD_ID,
            rule_id=pending.rule_id,
            request_id=request_id,
            confirmed_by=self.resident.actor_id,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._set_glow(GLOW_ACTING, target=self._device_room(proposal.target_device_id))
        receipt = self.runtime.run_action(
            principal=self.resident,
            action_kind=proposal.action_kind,
            target_device_id=proposal.target_device_id,
            target_selector=proposal.target_selector,
            parameters=proposal.parameters,
            justification=proposal.justification,
            world=self.house.snapshot(now),
            now=now,
            confirmation_token=token,
        )
        self.receipts.append(receipt)
        if receipt.decision.status == DecisionStatus.ALLOW:
            self._proposal_completed(proposal, receipt)
        elif receipt.decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._pending[request_id] = pending
            self._direct_actions[request_id] = proposal
            self._set_glow(GLOW_PERMISSION, target=self._device_room(proposal.target_device_id))
        else:
            self._record_block(receipt)
        self._publish_state()
        return self.state()

    def deny(self, request_id: str) -> bool:
        if request_id not in self._pending:
            return False
        del self._pending[request_id]
        self._direct_actions.pop(request_id, None)
        self._say("user", "No, leave it.")
        self._say("haven", "Understood — I'll leave the garage as it is.")
        self._set_glow(GLOW_IDLE)
        self._publish_state()
        return True

    def mark_camera_down(self) -> dict[str, Any]:
        self.house.set_camera_down(True)
        now = self._clock()
        receipt = self.runtime.run_rule(
            self.camera_rule_id,
            principal=self.resident,
            world=self.house.snapshot(now),
            justification="The driveway camera stopped reporting; HAVEN checked whether it can still see.",
            now=now,
        )
        self.receipts.append(receipt)
        self._say("haven", "I can't confirm the driveway camera is up.")
        if receipt.decision.status == DecisionStatus.UNAVAILABLE:
            # Fail closed: the blocked receipt means no command reached the
            # execution adapter, and the same rule runs again once evidence
            # is back.
            self._say("haven", "The driveway camera's evidence is unavailable; I won't act on guesswork.")
            self._set_glow(GLOW_CRITICAL, target=DRIVEWAY_ROOM)
        else:
            self._record_block(receipt)
        self._publish_state()
        return self.state()

    def mark_camera_up(self) -> dict[str, Any]:
        self.house.set_camera_down(False)
        now = self._clock()
        receipt = self.runtime.run_rule(
            self.camera_rule_id,
            principal=self.resident,
            world=self.house.snapshot(now),
            justification="The driveway camera is back online; running the clip rule again.",
            now=now,
        )
        self.receipts.append(receipt)
        self._say("haven", "The driveway camera is back online.")
        if receipt.decision.status == DecisionStatus.ALLOW:
            self._say("haven", "Evidence restored — I recorded a driveway clip.")
            self._set_glow(GLOW_COMPLETED, target=DRIVEWAY_ROOM)
        else:
            self._say("haven", f"I couldn't run the clip rule: {receipt.decision.explanation}")
            self._set_glow(GLOW_IDLE)
        self._publish_state()
        return self.state()

    # -- the scheduler: another requester over the same runtime ---------------

    def run_scheduler_tick(self, now: datetime | None = None) -> list:
        """Run one synchronous scheduler tick; returns the tick's receipts.

        Tests pass a fixed ``now`` to force a due window; the background loop
        and the HTTP endpoint leave it None and let the clock decide.
        """

        now = now or self._clock()
        world = self.house.snapshot(now)
        receipts = self.scheduler.tick(world=world, now=now)
        self.receipts.extend(receipts)
        for receipt in receipts:
            self._handle_scheduler_receipt(receipt)
        self._publish_state()
        return receipts

    def _handle_scheduler_receipt(self, receipt) -> None:
        # Mirror what the run_rule callers already publish: glow and state
        # move for every outcome; the conversation only speaks when the
        # schedule was told no -- a successful firing is quiet architectural
        # operation.
        if receipt.decision.status == DecisionStatus.ALLOW:
            result = receipt.device_result
            if result is not None and not result.success:
                self._say("haven", f"I was due to run that but the device refused: {result.detail}")
                self._set_glow(GLOW_CRITICAL, target=self._device_room(receipt.requested_action.target_device_id))
                return
            self._set_glow(GLOW_COMPLETED, target=self._device_room(receipt.requested_action.target_device_id))
            return
        try:
            rule = self.store.get_rule(receipt.requested_action.rule_id)
            summary = " ".join(rule.draft.source_text.split())
        except KeyError:
            summary = receipt.requested_action.rule_id
        self._say(
            "haven",
            f"I was due to run {summary} but {receipt.decision.code.value}: {receipt.decision.explanation}",
        )
        self._set_glow(GLOW_CRITICAL, target=self._device_room(receipt.requested_action.target_device_id))

    def start_scheduler(self) -> None:
        if self._scheduler_thread is not None:
            return
        stop = threading.Event()
        thread = threading.Thread(
            target=self._scheduler_loop,
            args=(stop,),
            daemon=True,
            name="haven-scheduler",
        )
        self._scheduler_stop = stop
        self._scheduler_thread = thread
        thread.start()

    def stop_scheduler(self) -> None:
        thread = self._scheduler_thread
        if thread is None:
            return
        assert self._scheduler_stop is not None
        self._scheduler_stop.set()
        thread.join(timeout=5)
        self._scheduler_thread = None
        self._scheduler_stop = None

    def _scheduler_loop(self, stop: threading.Event) -> None:
        # Dumb and lock-free on purpose: the engine's last_fired check is
        # single-threaded by design, and the demo only ticks from one place
        # at a time. A failed tick must not kill the loop.
        while not stop.wait(self._scheduler_tick_seconds):
            try:
                self.run_scheduler_tick()
            except Exception:
                pass

    def scheduler_status(self) -> list[dict[str, Any]]:
        now = self._clock()
        world = self.house.snapshot(now)
        return [
            serialize.scheduler_status_to_dict(status)
            for status in self.scheduler.status(self.store.state.rules, world=world, now=now)
        ]

    def set_scheduler_enabled(self, rule_id: str, enabled: bool) -> list[dict[str, Any]]:
        self.scheduler.set_enabled(rule_id, enabled)
        self._publish_state()
        return self.scheduler_status()

    def has_rule(self, rule_id: str) -> bool:
        return any(rule.rule_id == rule_id for rule in self.store.state.rules)

    def chat(self, text: str, focus: str | None = None) -> dict[str, Any]:
        self._say("user", text)
        self._chat_intent(text, focus=focus)
        self._publish_state()
        return self.state()

    def _chat_intent(self, text: str, focus: str | None = None) -> None:
        """Route one utterance through the provider's proposed intent.

        Classification lives in the intelligence seam: the director builds
        an AgentContext (bounded world + room focus) and the provider
        proposes exactly one intent, which is routed here. Nothing executes
        without crossing authority. Voice and typed text share this path;
        stop-phrases are intercepted before it (VoiceSession).
        """
        now = self._clock()
        intent = self.runtime.intelligence_provider.interpret_intent(
            text,
            context=self._agent_context(focus=focus, now=now),
            principal=self.resident,
            now=now,
        )
        if isinstance(intent, QueryRequest):
            self._answer_query(intent, focus=focus)
        elif isinstance(intent, ActionProposal):
            self._run_proposal(intent)
        elif isinstance(intent, RuleDraft):
            self._chat_rule_draft(intent, now)
        elif isinstance(intent, ClarificationRequest):
            self._say("haven", intent.question)
        else:
            normalized = " ".join(text.casefold().split()).rstrip(".!?")
            self._say("haven", self._conversation_fallback(normalized, focus))

    def _conversation_fallback(self, normalized: str, focus: str | None) -> str:
        if normalized in ("close the garage", "close the garage door") and not self.house.garage_open():
            return "The garage is already closed."
        if any(normalized == pattern for pattern in self._LIGHT_OFF_PATTERNS) and focus is None:
            return "Which room do you mean?"
        room = self._light_room(normalized) or (
            focus if any(normalized == p for p in self._LIGHT_OFF_PATTERNS) else None
        )
        if room is not None and "light" in normalized and not self.registry.find(role="light", room=room):
            return f"I don't see a light in the {room}."
        return "I can't do that yet."

    # -- the deterministic classification table (now in the seam) -------------

    _LIGHT_OFF_PATTERNS = (
        "turn that light off",
        "turn off the light",
        "turn off that light",
        "turn the light off",
    )

    def _light_room(self, normalized: str) -> str | None:
        for pattern in (
            "turn off the {room} light",
            "turn the {room} light off",
            "turn off the light in the {room}",
        ):
            prefix, _, suffix = pattern.partition("{room}")
            if normalized.startswith(prefix) and normalized.endswith(suffix):
                end = len(normalized) - len(suffix) if suffix else len(normalized)
                room = normalized[len(prefix):end].strip()
                if room and " " not in room:
                    return room
        return None

    def _chat_rule_draft(self, draft: RuleDraft, now: datetime) -> None:
        """A proposed rule draft from chat: the propose -> approve lifecycle."""
        rule = self.runtime.propose_draft(draft, principal=self.resident, now=now)
        result = self.runtime.approve_rule(
            rule.rule_id,
            principal=self.owner,
            justification="Owner approved the automation the resident asked for in chat.",
            now=now,
        )
        if result.decision.status == DecisionStatus.ALLOW:
            self._say("haven", f"Done — I've set that up: {rule.draft.interpretation}")
        elif result.decision.status == DecisionStatus.NEEDS_CLARIFICATION:
            self._say("haven", "I drafted a rule from that, but it needs clarification before I can set it up.")
        else:
            self._say("haven", f"I couldn't set that up: {result.decision.explanation}")

    # -- query handling -----------------------------------------------------

    def _answer_query(self, intent: QueryRequest, *, focus: str | None) -> None:
        provider = self.runtime.intelligence_provider
        if isinstance(provider, ModelIntelligenceProvider) and provider.chat_handle() is not None:
            try:
                reply = provider.chat(self._agent_context(focus=focus, now=self._clock()), intent.text)
            except UnsupportedIntent:
                pass  # the model path failed; the deterministic world answers
            else:
                self._say("haven", reply.text)
                return
        self._say("haven", self._deterministic_answer(intent.text))

    def _deterministic_answer(self, text: str) -> str:
        normalized = " ".join(text.casefold().split()).rstrip(".!?")
        now = self._clock()
        view = self._world_view(now)
        if "garage" in normalized and ("open" in normalized or "closed" in normalized):
            device = next((item for item in view.devices if item.device_id == "garage_door"), None)
            if device is None:
                return "I have no evidence about the garage door."
            return "The garage door is open." if device.is_on else "The garage door is closed."
        room = self._queried_light_room(normalized)
        if room is not None:
            device = next(
                (item for item in view.devices if item.room_id == room and item.kind == "light"), None
            )
            if device is None:
                return f"I don't see a light in the {room}."
            return f"The {room} light is {'on' if device.is_on else 'off'}."
        if normalized in ("who is home", "who's home", "who is at home", "who's at home"):
            home = sorted(
                self._person_name(item.person_id) for item in view.presence if item.present
            )
            if not home:
                return "Nobody is home right now."
            return f"{' and '.join(home)} {'is' if len(home) == 1 else 'are'} home."
        return "I can't answer that without an intelligence model."

    _LIGHT_QUERY_PATTERNS = (
        "is the {room} light on",
        "is the light on in the {room}",
        "is the {room} light off",
    )

    def _queried_light_room(self, normalized: str) -> str | None:
        if "light" not in normalized:
            return None
        for pattern in self._LIGHT_QUERY_PATTERNS:
            prefix, _, suffix = pattern.partition("{room}")
            if normalized.startswith(prefix) and normalized.endswith(suffix):
                room = normalized[len(prefix): len(normalized) - len(suffix) if suffix else None].strip()
                if room and " " not in room:
                    return room
        return None

    def _world_view(self, now: datetime) -> WorldView:
        return WorldView.from_snapshot(
            self.house.snapshot(now),
            now=now,
            device_registry=self.registry,
            names=self._world_names(),
            recent_events=self.store.events,
        )

    def _agent_context(self, *, focus: str | None, now: datetime) -> AgentContext:
        return AgentContext(
            household_id=HOUSEHOLD_ID,
            actor_id=self.resident.actor_id,
            actor_role=self.resident.role_tier.name,
            room_focus=focus,
            recent_lines=tuple(entry["text"] for entry in self._conversation[-6:]),
            world=self._world_view(now),
        )

    def _world_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for manifest in self.registry.all_devices():
            names[manifest.device_id] = f"{manifest.role} · {manifest.device_id}"
            if manifest.room:
                names[manifest.room] = manifest.room.replace("_", " ").title()
        return names

    # -- action proposals ---------------------------------------------------

    def _run_proposal(self, proposal: ActionProposal) -> None:
        now = self._clock()
        try:
            receipt = self.runtime.run_action(
                principal=self.resident,
                action_kind=proposal.action_kind,
                target_device_id=proposal.target_device_id,
                target_selector=proposal.target_selector,
                parameters=proposal.parameters,
                justification=proposal.justification,
                world=self.house.snapshot(now),
                now=now,
            )
        except ValueError:
            self._clarify_selector(proposal)
            return
        self.receipts.append(receipt)
        status = receipt.decision.status
        if status == DecisionStatus.ALLOW:
            self._proposal_completed(proposal, receipt)
        elif status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._ask_direct_action(proposal, receipt, now)
        else:
            self._record_block(receipt)

    def _clarify_selector(self, proposal: ActionProposal) -> None:
        selector = proposal.target_selector
        resolved = self.registry.resolve(selector) if selector is not None else ()
        if selector is not None and selector.room is not None and resolved:
            self._say("haven", f"Which one? There are {len(resolved)} lights in the {selector.room}.")
        else:
            self._say("haven", "Which one? I couldn't resolve that to a single device.")

    def _proposal_completed(self, proposal: ActionProposal, receipt) -> None:
        result = receipt.device_result
        if result is not None and not result.success:
            # Authority allowed it but the device refused the command; say so.
            self._say("haven", f"I couldn't do that: {result.detail}")
            self._set_glow(GLOW_CRITICAL, target=self._device_room(receipt.requested_action.target_device_id))
            return
        target = receipt.requested_action.target_device_id
        room = self._device_room(target)
        if proposal.action_kind is ActionKind.TURN_LIGHT_OFF:
            self._say("haven", f"Done — the {room} light is off.")
        elif proposal.action_kind is ActionKind.CLOSE_GARAGE:
            self._say("haven", "The garage door is closed.")
        elif proposal.action_kind is ActionKind.OPEN_GARAGE:
            self._say("haven", "The garage door is open.")
        else:
            self._say("haven", "Done.")
        self._set_glow(GLOW_COMPLETED, target=room)

    def _ask_direct_action(self, proposal: ActionProposal, receipt, now: datetime) -> None:
        request = receipt.requested_action
        rule_id = request.rule_id
        if proposal.action_kind is ActionKind.OPEN_GARAGE:
            title = "Open the garage door?"
            detail = "You asked to open it directly; this action needs your confirmation."
        else:
            title = "Close the garage door?"
            detail = (
                f"It has been open {self.house.garage_open_minutes(at=now)} minutes. "
                "You asked to close it directly; this action needs your confirmation."
            )
        pending = PendingRequest(
            request_id=request.request_id,
            rule_id=rule_id,
            title=title,
            detail=detail,
            expires_at=now + timedelta(minutes=5),
        )
        self._pending[pending.request_id] = pending
        self._direct_actions[pending.request_id] = proposal
        self._say("haven", "Want me to do that?")
        self._set_glow(GLOW_PERMISSION, target=self._device_room(request.target_device_id))

    def voice_focus(self) -> str | None:
        """Room voice input resolves relative to: the present resident's room."""
        for item in self.house.snapshot(self._clock()).presence:
            if item.present:
                return item.room_id
        return None

    def voice_wake(self) -> dict[str, Any]:
        ok = self.voice.wake()
        return {"ok": ok, "state": self.state()}

    def voice_utterance(self, text: str) -> dict[str, Any]:
        ok = self.voice.utterance(text)
        return {"ok": ok, "state": self.state()}

    def voice_cancel(self) -> dict[str, Any]:
        ok = self.voice.cancel()
        return {"ok": ok, "state": self.state()}

    def reset(self) -> dict[str, Any]:
        # Keep the background tick from firing into a half-reset house: stop
        # it first and restart it afterwards if it was running.
        scheduler_running = self._scheduler_thread is not None
        if scheduler_running:
            self.stop_scheduler()
        self.voice.reset()
        self._pending.clear()
        self._direct_actions.clear()
        self._conversation.clear()
        self._auto_allow.clear()
        self._glow = GLOW_IDLE
        self._glow_target = None
        self.start_scenario()
        if scheduler_running:
            self.start_scheduler()
        return self.state()

    def state(self) -> dict[str, Any]:
        now = self._clock()
        world = self.house.snapshot(now)
        present = {item.person_id: item.room_id for item in world.presence if item.present}
        rooms = []
        for manifest in self.registry.all_devices():
            room_id = manifest.room or "unassigned"
            room = next((existing for existing in rooms if existing["id"] == room_id), None)
            if room is None:
                room = {"id": room_id, "devices": [], "people": [], "camera": None}
                rooms.append(room)
            device = world.device_for(manifest.device_id)
            if device is not None:
                room["devices"].append(serialize.device_to_dict(device, role=manifest.role))
            if manifest.device_type == "camera":
                camera_state = world.device_for(manifest.device_id)
                room["camera"] = serialize.camera_to_dict(
                    camera_id=manifest.device_id,
                    label=manifest.device_id.removesuffix("_cam").removesuffix("_camera").replace("_", " ").title(),
                    motion=self.house.motion_detected(),
                    online=camera_state is not None and camera_state.status != EvidenceStatus.UNAVAILABLE,
                )
        for room in rooms:
            room["people"] = sorted(
                self._person_name(person_id) for person_id, room_id in present.items() if room_id == room["id"]
            )
        rooms_payload = [
            serialize.room_to_dict(
                room_id=room["id"],
                name=room["id"].replace("_", " ").title(),
                devices=room["devices"],
                people=room["people"],
                camera=room["camera"],
            )
            for room in rooms
        ]
        contexts = [
            serialize.context_to_dict(
                context_id=item.context_id,
                label=self.CONTEXT_LABELS.get(item.context_id, item.context_id),
                active=item.active,
            )
            for item in world.contexts
        ]
        people = [
            serialize.person_to_dict(person_id=person_id, name=self._person_name(person_id), room=room_id)
            for person_id, room_id in present.items()
        ]
        if self._glow == GLOW_CRITICAL and self.house.camera_down():
            line = CAMERA_BLIND_LINE
        else:
            line = ATTENTION_LINE if (self._pending or self._glow in (GLOW_PERMISSION, GLOW_CRITICAL)) else NOMINAL_LINE
        return serialize.state_to_dict(
            glow=self._glow,
            glow_target=self._glow_target,
            revision=self.store.state.revision,
            rooms=rooms_payload,
            contexts=contexts,
            people=people,
            pending=[serialize.pending_to_dict(item) for item in self._pending.values()],
            conversation=[serialize.message_to_dict(sender=m["from"], text=m["text"]) for m in self._conversation],
            activity=self._activity_payload(),
            memory=self._memory_payload(),
            automations=self._automations_payload(),
            scheduler=[
                serialize.scheduler_status_to_dict(status)
                for status in self.scheduler.status(self.store.state.rules, world=world, now=now)
            ],
            system=serialize.system_to_dict(
                revision=self.store.state.revision,
                event_count=len(self.store.events),
                memory_count=len(self.store.state.memory),
                engine=serialize.engine_to_dict(
                    human_override_window=self.engine.human_override_window,
                    minimum_confidence=self.engine.minimum_confidence,
                ),
                providers=[
                    serialize.provider_to_dict(caps) for caps in self.capability_registry.registered()
                ],
            ),
            voice=self.voice.to_dict(),
            status=serialize.status_to_dict(
                devices=len(tuple(self.registry.all_devices())), people=len(people), line=line
            ),
        )

    def _activity_payload(self) -> list[dict[str, Any]]:
        rules_by_id = {rule.rule_id: rule.draft.source_text for rule in self.store.state.rules}
        actions_by_id = {action.action_id: action for action in self.store.state.actions}
        rows = []
        for event in reversed(self.store.events[-ACTIVITY_LIMIT:]):
            payload = dict(event.payload)
            action = actions_by_id.get(str(payload.get("action_id")))
            rows.append(
                serialize.event_to_dict(
                    event,
                    rule_label=rules_by_id.get(str(payload.get("rule_id"))),
                    action_device=action.request.target_device_id if action is not None else None,
                )
            )
        return rows

    def _automations_payload(self) -> list[dict[str, Any]]:
        rules = sorted(self.store.state.rules, key=_rule_recency_key)
        return [
            serialize.rule_to_dict(
                rule,
                device_room=(
                    self._device_room(rule.draft.target_device_id)
                    if rule.draft.target_device_id is not None
                    else None
                ),
            )
            for rule in rules
        ]

    def _memory_payload(self) -> list[dict[str, Any]]:
        return [
            serialize.memory_to_dict(entry) for entry in reversed(self.store.state.memory[-MEMORY_LIMIT:])
        ]

    @staticmethod
    def _person_name(person_id: str) -> str:
        return person_id.replace("_", " ").title()

    def _record_block(self, receipt) -> None:
        self._say("haven", f"I couldn't do that: {receipt.decision.explanation}")
        self._set_glow(GLOW_CRITICAL, target=self._device_room(receipt.requested_action.target_device_id))

    def _room_for_rule(self, rule_id: str) -> str | None:
        try:
            rule = self.store.get_rule(rule_id)
        except KeyError:
            return None
        if rule.draft.target_device_id is not None:
            return self._device_room(rule.draft.target_device_id)
        selector = rule.draft.target_selector
        return selector.room if selector is not None else None

    def _device_room(self, device_id: str) -> str | None:
        if self.registry.is_registered(device_id):
            return self.registry.get(device_id).room
        return None

    def _say(self, sender: str, text: str) -> None:
        self._conversation.append({"from": sender, "text": text})

    def _set_glow(self, glow: str, target: Any = _KEEP_TARGET) -> None:
        if glow == GLOW_IDLE:
            target = None
        elif target is _KEEP_TARGET:
            target = self._glow_target
        if glow == self._glow and target == self._glow_target:
            return
        self._glow = glow
        self._glow_target = target
        self._publish("glow", {"state": glow, "target": target})

    def _publish_state(self) -> None:
        self._publish("state", self.state())

    def _publish(self, kind: str, payload: Any) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put((kind, payload))

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


__all__ = [
    "DemoDirector",
    "PendingRequest",
    "SimulatedExecutionAdapter",
    "SimulatedHouse",
    "VoiceSession",
    "build_registry",
]
