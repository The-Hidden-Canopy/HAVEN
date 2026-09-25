"""HavenApplication: the production controller behind the local web surface.

This is the only place the web layer talks to: the controller owns a
``HavenRuntime`` wired to whatever ``WorldProvider``/``DeviceRegistry``/
``ExecutionProviderRegistry`` the composition root built, interprets a
handful of chat intents, keeps the pending-confirmation bookkeeping the
runtime cannot see, and publishes ``(kind, payload)`` events to subscribers
(``server.py`` turns those into SSE). Glow is derived only from engine
outcomes.

``HavenApplication`` imports no simulated-house assumptions, fixture
identities, or demo scenario -- it never constructs a ``SimulatedHouse``,
never defaults a household to the literal ``"household-demo"``/``"gerron"``
ids, and never seeds a scripted automation. ``DemoDirector``
(``haven.web.demo``) is the thin subclass that adds exactly those things for
the local fixture household; the real-mode composition root
(``haven.web.application.build_application``) constructs this class
directly instead. Everything else -- chat, permissions, voice, scheduling,
state projection, persistence -- lives here once, shared by both.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Mapping
from uuid import uuid4

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ConfirmationToken,
    DecisionStatus,
    EvidenceStatus,
    Principal,
    Rule,
    RuleDraft,
    RuleStatus,
    RoleTier,
    ScheduleTrigger,
)
from haven.core.store import HavenStore
from haven.core.world import WorldProvider
from haven.devices import DeviceRegistry
from haven.errors import ScopeViolation
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
from .history_persist import HistoryStore
from .rules_persist import RulesPersistence

GLOW_IDLE = "idle"
GLOW_ACTING = "acting"
GLOW_PERMISSION = "permission"
GLOW_CRITICAL = "critical"
GLOW_COMPLETED = "completed"

ATTENTION_LINE = "HAVEN needs your attention"
NOMINAL_LINE = "Everything nominal"
CAMERA_BLIND_LINE = "HAVEN can't see clearly"
NO_OWNER_DECLARED_LINE = "No one has declared themselves as this household's owner yet -- add a person in Setup first."

ACTIVITY_LIMIT = 20
MEMORY_LIMIT = 20

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
    # Who asked, and -- for an externally initiated request -- which
    # external-agent connection it came through. Both stay None for
    # requests HAVEN's own surfaces raise on the resident's behalf.
    requested_by: str | None = None
    external_connection_id: str | None = None


VOICE_DORMANT = "dormant"
VOICE_WAKE = "wake"
VOICE_LISTENING = "listening"
VOICE_INTERPRETING = "interpreting"
VOICE_SPEAKING = "speaking"

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
    """Wake-word → utterance state machine owned by the ``HavenApplication``.

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
        director: "HavenApplication",
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

    def sync_external_state(self, state: str) -> None:
        """Report a state change from a real `SpeechService` pump.

        The two state vocabularies already share their string values
        (dormant/wake/listening/interpreting, plus `SpeechService`'s own
        "speaking"), so this takes the value verbatim rather than
        translating. Real voice runs independently of this session's own
        wake/timer machinery -- this only makes the reported `voice.state`
        reflect whichever one is actually active.
        """

        self._enter(state)

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


class HavenApplication:
    """Owns the runtime and publishes state/glow events for a real household.

    HAVEN acts as the household's declared people: every governed flow asks
    authority as `resident` or `owner`. A household that has connected a
    provider but declared nobody yet gets a distinctly-named, structurally
    powerless placeholder identity (`has_declared_owner` is what actually
    keeps it powerless -- see `_chat_intent`/`device_command` -- never this
    placeholder's actor_id on its own); `DemoDirector` always supplies its
    own concrete identity instead of relying on this fallback.

    `house`/`adapter` default to `None` here and stay `None` for every real
    instance -- only `DemoDirector` ever sets them, before it calls
    `super().__init__()`. Anything that reads `director.house is None` as
    "is this the demo fixture" gets an honest answer for both classes.
    """

    house: Any | None = None
    adapter: Any | None = None
    CONTEXT_LABELS = {"working_late": "Working late", "vacation_mode": "Vacation mode"}

    def __init__(
        self,
        *,
        world: WorldProvider,
        household_id: str,
        clock: Clock | None = None,
        voice_ack_seconds: float = VOICE_ACK_SECONDS,
        voice_refractory_seconds: float = VOICE_REFRACTORY_SECONDS,
        capability_registry: CapabilityRegistry | None = None,
        model_manager: ModelManager | None = None,
        scheduler_tick_seconds: float = 20.0,
        registry: DeviceRegistry | None = None,
        execution: ExecutionProviderRegistry | None = None,
        voice_enabled: bool = True,
        intelligence_provider=None,
        ha_states_source=None,
        person_names: Mapping[str, str] | None = None,
        person_roles: Mapping[str, str] | None = None,
        room_names: Mapping[str, str] | None = None,
        rules_persistence: RulesPersistence | None = None,
        resident: Principal | None = None,
        owner: Principal | None = None,
        history: HistoryStore | None = None,
    ) -> None:
        self.household_id = household_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        now = self._clock()
        self.world = world
        self.registry = registry if registry is not None else DeviceRegistry()
        providers = execution if execution is not None else ExecutionProviderRegistry()
        self.store = HavenStore(household_id=self.household_id)
        self.capability_registry = capability_registry or build_default_registry()
        self.engine = AuthorityEngine(device_registry=self.registry)
        # The bridge makes models an upgrade path in front of the scripted
        # floor: with no model loaded every chat falls through to the
        # deterministic behavior, exactly as before. An injected provider
        # (e.g. the plain scripted floor when intelligence is disabled) is
        # used verbatim.
        self.model_manager = model_manager if model_manager is not None else ModelManager()
        provider = (
            intelligence_provider
            if intelligence_provider is not None
            else ModelIntelligenceProvider(ScriptedIntelligenceProvider(), self.model_manager)
        )
        self.runtime = HavenRuntime(
            store=self.store,
            intelligence_provider=provider,
            authority=self.engine,
            execution_providers=providers,
        )
        # A real household with nobody declared yet gets this distinctly-named
        # placeholder rather than any fixture identity -- `has_declared_owner`
        # is what actually keeps it powerless, never this actor_id string.
        self.resident = (
            resident
            if resident is not None
            else Principal(actor_id="no_owner_declared", household_id=self.household_id, role_tier=RoleTier.MEMBER)
        )
        self.owner = (
            owner
            if owner is not None
            else Principal(actor_id="no_owner_declared", household_id=self.household_id, role_tier=RoleTier.OWNER)
        )
        # False only for a real household with nobody declared yet -- the
        # state `_chat_intent`/`device_command` refuse to act under.
        # `DemoDirector` always supplies a concrete owner, so this is always
        # true there.
        self.has_declared_owner = owner is not None
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._glow = GLOW_IDLE
        self._glow_target: str | None = None
        self._conversation: list[dict[str, Any]] = []
        self._pending: dict[str, PendingRequest] = {}
        self._direct_actions: dict[str, ActionProposal] = {}
        self._auto_allow: set[str] = set()
        self.receipts: list = []
        # Set for real before `_seed_initial_rules`/rules-restore below,
        # since both can call `_publish_state()` before the history restore
        # point further down ever runs.
        self._history: HistoryStore | None = None
        self._persisted_event_count = 0
        self._persisted_memory_count = 0
        self._persisted_receipt_count = 0
        self.voice_enabled = voice_enabled
        # Real-mode HA states source for the setup discovery scan; None when
        # no provider is attached (the scan then lists only local candidates).
        self.ha_states_source = ha_states_source
        # Declared person names, when a setup layer declares who lives here:
        # the declared name wins over the title-cased person_id derivation.
        self._person_names = dict(person_names or {})
        # Declared household standing ("owner"/"member") per person id --
        # the only input `principal_for` uses to derive a role tier. Nothing
        # an external caller sends can change it.
        self._person_roles = dict(person_roles or {})
        # Declared room names are the same kind of household-owned meaning:
        # provider observations may add rooms, but cannot erase a room the
        # user explicitly declared or force its display name back to an id.
        self._room_names = dict(room_names or {})
        self._scheduler_tick_seconds = scheduler_tick_seconds
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_stop: threading.Event | None = None
        self.voice = VoiceSession(
            self,
            ack_seconds=voice_ack_seconds,
            refractory_seconds=voice_refractory_seconds,
        )
        # A real always-on SpeechService, when `start_voice()` manages to
        # build one; None means real voice is unavailable on this host (no
        # native audio device, or no wake+ASR model pair loaded) and the
        # simulated `VoiceSession` above is the only voice surface.
        self._speech_service = None
        # A standalone TTS synthesizer + speaker, independent of the pump
        # above: a household's wake-word/ASR input may come from somewhere
        # else entirely, but whatever HAVEN says is worth actually speaking
        # whenever a TTS model is assigned. None means no TTS model is
        # assigned/loaded, or no speaker device is available.
        self._tts_synthesizer = None
        self._tts_sink = None
        # The scheduler is another requester over the same runtime, asking as
        # the resident.
        self._rules_persistence = rules_persistence
        self.scheduler = SchedulerEngine(runtime=self.runtime, principal=self.resident)
        # Hook: a subclass seeds its own starting automations here, before
        # any persisted rules are restored on top (see `DemoDirector`, which
        # seeds its fixture scenario). The production controller has none.
        self._seed_initial_rules(now)
        if self._rules_persistence is not None:
            # Rehydrate automations saved by a previous process lifetime. The
            # restore replaces the rule set wholesale (the transitions that
            # built these rules already happened and were audited then); the
            # scheduler enabled map is applied only for rule ids the restored
            # store actually holds, so seeded rules keep their set unless the
            # file says otherwise.
            restored = self._rules_persistence.load()
            if restored.rules:
                try:
                    self.store.restore_rules(restored.rules)
                except ScopeViolation:
                    # rules.json was written under a different household_id
                    # than this run's -- e.g. a pre-existing file from before
                    # a real household had a permanent id of its own. Boot
                    # must not crash on stale automations it can no longer
                    # trust the scope of; they are dropped, not replayed.
                    pass
            restored_ids = {rule.rule_id for rule in self.store.state.rules}
            for rule_id, enabled in restored.scheduler_enabled.items():
                if rule_id in restored_ids:
                    self.scheduler.set_enabled(rule_id, enabled)
        self._history = history
        if self._history is not None:
            # Same restore discipline as rules: history enters as
            # already-decided fact, replaying no transitions and emitting no
            # new events. A household-id mismatch (e.g. a history.db from
            # before this installation had a permanent id) drops the stale
            # rows rather than crashing boot, the same as the rules sidecar.
            snapshot = self._history.load(self.household_id)
            try:
                self.store.restore_events(snapshot.events)
                self.store.restore_actions(snapshot.actions)
                self.store.restore_memory(snapshot.memory)
            except ScopeViolation:
                pass
            else:
                self.receipts.extend(snapshot.receipts)
        # Cursors into the append-only event/receipt sequences: only the
        # slice past these indices is new since the last persist, so a
        # publish that fires on every glow change (most of them) does not
        # re-write a household's entire history each time. `actions` has no
        # cursor: HavenStore replaces an action's row in place as it moves
        # AUTHORIZED -> EXECUTED, so `_persist_history` upserts all of them
        # every time instead -- correct, and cheap at this data's scale.
        self._persisted_event_count = len(self.store.events)
        self._persisted_memory_count = len(self.store.state.memory)
        self._persisted_receipt_count = len(self.receipts)

    def _seed_initial_rules(self, now: datetime) -> None:
        """Hook: a subclass seeds its own starting automations here, called
        before any persisted rules are restored on top. The production
        controller seeds nothing of its own."""

        self.garage_rule_id = None
        self.camera_rule_id = None
        self.office_light_rule_id = None

    @property
    def pending_requests(self) -> tuple[PendingRequest, ...]:
        return tuple(self._pending.values())

    def principal_for(self, person_id: str) -> Principal | None:
        """The HAVEN principal for one declared person, or None if unknown.

        Role derives only from this household's own declarations: the
        declared owner is OWNER, every other declared person is MEMBER. An
        undeclared id (or a household with nobody declared) resolves to
        nothing, so nothing can be bound to a placeholder identity.
        """

        if not self.has_declared_owner or not isinstance(person_id, str) or not person_id.strip():
            return None
        person_id = person_id.strip()
        if person_id == self.owner.actor_id or self._person_roles.get(person_id) == "owner":
            return Principal(actor_id=person_id, household_id=self.household_id, role_tier=RoleTier.OWNER)
        if person_id == self.resident.actor_id or person_id in self._person_roles or person_id in self._person_names:
            return Principal(actor_id=person_id, household_id=self.household_id, role_tier=RoleTier.MEMBER)
        return None

    def _persist_rules(self) -> None:
        # The rules sidecar is optional: without one this is a no-op and
        # nothing here is any more persistent than process memory.
        if self._rules_persistence is None:
            return
        enabled = {
            rule.rule_id: self.scheduler.is_enabled(rule.rule_id) for rule in self.store.state.rules
        }
        self._rules_persistence.save(self.store.state.rules, enabled)

    def _rule_completion_line(self, rule_id: str) -> str:
        """A generic "done" line for an approved rule, derived from what it
        actually did -- never a fixed phrase naming one specific device."""

        try:
            rule = self.store.get_rule(rule_id)
        except KeyError:
            return "Done."
        action_kind = rule.draft.action_kind
        if action_kind is ActionKind.CLOSE_GARAGE:
            return "Done — the garage door is closed."
        if action_kind is ActionKind.OPEN_GARAGE:
            return "Done — the garage door is open."
        if action_kind is ActionKind.TURN_LIGHT_OFF:
            room = self._room_for_rule(rule_id)
            if room is not None:
                return f"Done — the {room} light is off."
        return "Done."

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
            household_id=self.household_id,
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
            world=self.world.observe(now),
            justification="Owner approved the pending request.",
            now=now,
            confirmation_token=token,
        )
        self.receipts.append(receipt)
        if receipt.decision.status == DecisionStatus.ALLOW:
            self._say("haven", self._rule_completion_line(pending.rule_id))
            self._set_glow(GLOW_COMPLETED)
        elif receipt.decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._pending[request_id] = pending
            self._set_glow(GLOW_PERMISSION, target=self._room_for_rule(pending.rule_id))
        else:
            self._record_block(receipt)
        self._persist_rules()
        self._publish_state()
        return self.state()

    def _approve_direct_action(self, request_id: str, pending: PendingRequest) -> dict[str, Any] | None:
        self._confirm_direct_action(request_id, pending, principal=self.resident)
        self._publish_state()
        return self.state()

    def _confirm_direct_action(
        self,
        request_id: str,
        pending: PendingRequest,
        *,
        principal: Principal,
        external_source: tuple[tuple[str, str], ...] | None = None,
    ):
        """Consume one pending direct action with a fresh bound confirmation.

        The confirmation token names `principal` as the confirmer, so the
        authority engine's token binding checks the same person who is now
        acting -- the resident for HAVEN's own surfaces, the bound person
        for an external agent. Returns the receipt.
        """

        proposal = self._direct_actions.pop(request_id)
        del self._pending[request_id]
        now = self._clock()
        token = ConfirmationToken(
            token_id=_new_id("confirm"),
            household_id=self.household_id,
            rule_id=pending.rule_id,
            request_id=request_id,
            confirmed_by=principal.actor_id,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._set_glow(GLOW_ACTING, target=self._device_room(proposal.target_device_id))
        receipt = self.runtime.run_action(
            principal=principal,
            action_kind=proposal.action_kind,
            capability=proposal.capability,
            target_device_id=proposal.target_device_id,
            target_selector=proposal.target_selector,
            parameters=proposal.parameters,
            justification=proposal.justification,
            world=self.world.observe(now),
            now=now,
            confirmation_token=token,
        )
        if external_source is not None:
            receipt = replace(receipt, external_source=external_source)
        self.receipts.append(receipt)
        if receipt.decision.status == DecisionStatus.ALLOW:
            self._proposal_completed(proposal, receipt)
        elif receipt.decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._pending[request_id] = pending
            self._direct_actions[request_id] = proposal
            self._set_glow(GLOW_PERMISSION, target=self._device_room(proposal.target_device_id))
        else:
            self._record_block(receipt)
        return receipt

    def deny(self, request_id: str) -> bool:
        if request_id not in self._pending:
            return False
        del self._pending[request_id]
        self._direct_actions.pop(request_id, None)
        self._say("user", "No, leave it.")
        self._say("haven", "Understood — I'll leave it as it is.")
        self._set_glow(GLOW_IDLE)
        self._publish_state()
        return True

    def mark_camera_down(self) -> dict[str, Any]:
        # Camera-down simulation is demo-only; a live world cannot be marked
        # down from here (its cameras report their own real availability).
        return {"ok": False, "state": self.state()}

    def mark_camera_up(self) -> dict[str, Any]:
        return {"ok": False, "state": self.state()}

    # -- the scheduler: another requester over the same runtime ---------------

    def run_scheduler_tick(self, now: datetime | None = None) -> list:
        """Run one synchronous scheduler tick; returns the tick's receipts.

        Tests pass a fixed ``now`` to force a due window; the background loop
        and the HTTP endpoint leave it None and let the clock decide.
        """

        now = now or self._clock()
        world = self.world.observe(now)
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

    def start_voice(self) -> bool:
        """Build and start real voice I/O, as much of it as is possible.

        Two independent pieces, either or both may come up: the always-on
        wake+ASR capture pump (`SpeechService`, via `build_speech_service`),
        and standalone TTS playback for whatever HAVEN says (via
        `resolve_tts_synthesizer`) -- a household's wake-word/transcription
        input may come from somewhere else entirely, so TTS output must not
        depend on the capture pump existing. Returns whether the capture
        pump is running (the "is real voice INPUT active" signal callers
        already expect); check `self._tts_sink is not None` separately for
        output. Never raises: no native audio device, no models assigned,
        or any other reason either piece cannot be assembled just means
        that piece stays absent and the simulated `VoiceSession`
        (`voice_wake`/`voice_utterance`) remains the fallback for input --
        exactly as before this method existed. Safe to call again after
        `stop_voice()`.
        """

        self._start_tts_playback()
        if self._speech_service is not None:
            return True
        from .voice_runtime import build_speech_service

        try:
            service = build_speech_service(
                model_manager=self.model_manager,
                on_utterance=self._on_real_utterance,
                on_speech_state=self._on_real_speech_state,
            )
        except Exception:
            return False
        if service is None:
            return False
        try:
            service.start()
        except Exception:
            return False
        self._speech_service = service
        return True

    def _start_tts_playback(self) -> None:
        if self._tts_sink is not None:
            return
        from .voice_runtime import resolve_tts_synthesizer

        try:
            synthesizer = resolve_tts_synthesizer(self.model_manager)
        except Exception:
            return
        if synthesizer is None:
            return
        try:
            from haven.speech.native_audio import WinMMSpeakerSink

            sink = WinMMSpeakerSink()
        except Exception:
            return
        self._tts_synthesizer = synthesizer
        self._tts_sink = sink

    def stop_voice(self) -> None:
        service = self._speech_service
        self._speech_service = None
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass
        if self._tts_sink is not None:
            try:
                self._tts_sink.stop()
            except Exception:
                pass
        self._tts_synthesizer = None
        self._tts_sink = None

    def _on_real_utterance(self, final) -> None:
        """`SpeechService.on_utterance`: a real committed transcript.

        Mirrors `VoiceSession.utterance()`'s own post-transcript handling
        exactly -- say it, check for a stop-phrase, else route it through
        the same `_chat_intent` every typed and simulated-voice utterance
        already goes through -- so a real "Haven, turn off the office
        light" reaches authority and a device the identical way a typed one
        does.
        """

        self._say("user", final.text)
        if _is_stop_utterance(final.text):
            if self.pending_requests:
                for pending in self.pending_requests:
                    self.deny(pending.request_id)
            else:
                self._say("haven", STOPPED_LINE)
                self._publish_state()
        else:
            self._chat_intent(final.text, focus=self.voice_focus())
            self._publish_state()

    def _on_real_speech_state(self, state: str) -> None:
        self.voice.sync_external_state(state)

    def close_history(self) -> None:
        if self._history is not None:
            self._persist_history()
            self._history.close()

    def _scheduler_loop(self, stop: threading.Event) -> None:
        # Dumb and lock-free on purpose: the engine's last_fired check is
        # single-threaded by design, and this only ticks from one place at a
        # time. A failed tick must not kill the loop.
        while not stop.wait(self._scheduler_tick_seconds):
            try:
                self.run_scheduler_tick()
            except Exception:
                pass

    def scheduler_status(self) -> list[dict[str, Any]]:
        now = self._clock()
        world = self.world.observe(now)
        return [
            serialize.scheduler_status_to_dict(status)
            for status in self.scheduler.status(self.store.state.rules, world=world, now=now)
        ]

    def set_scheduler_enabled(self, rule_id: str, enabled: bool) -> list[dict[str, Any]]:
        self.scheduler.set_enabled(rule_id, enabled)
        self._persist_rules()
        self._publish_state()
        return self.scheduler_status()

    def has_rule(self, rule_id: str) -> bool:
        return any(rule.rule_id == rule_id for rule in self.store.state.rules)

    def automation_options(self) -> list[dict[str, Any]]:
        """Return manifest-backed writable controls for the authoring UI."""

        options: list[dict[str, Any]] = []
        for manifest in self.registry.all_devices():
            for capability in manifest.capabilities:
                if not capability.writable or not capability.service:
                    continue
                options.append(
                    {
                        "device_id": manifest.device_id,
                        "device_type": manifest.device_type,
                        "room": manifest.room,
                        "capability": capability.name,
                        "service": capability.service,
                        "control_class": capability.control_class.value,
                    }
                )
        return options

    def create_automation(
        self,
        *,
        source_text: str,
        time_of_day: str,
        weekdays: list[int] | tuple[int, ...] = (),
        target_device_id: str,
        capability: str | None = None,
        service: str | None = None,
        interpretation: str | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a proposed automation from a structured UI declaration.

        This is deliberately proposal-only. The caller must separately run
        `approve_automation`, which crosses the existing owner authority and
        transition/event path before the scheduler can execute the rule.
        """

        if not self.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not isinstance(source_text, str) or not source_text.strip():
            return {"ok": False, "error": "a non-empty 'source_text' is required"}
        if not isinstance(target_device_id, str) or not target_device_id.strip():
            return {"ok": False, "error": "a non-empty 'target_device_id' is required"}
        try:
            parsed_time = dt_time.fromisoformat(str(time_of_day).strip())
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": "time_of_day must be an HH:MM or HH:MM:SS value"}
        try:
            days = frozenset(weekdays)
            if any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6 for day in days):
                raise ValueError
            schedule = ScheduleTrigger(time_of_day=parsed_time, weekdays=days)
        except (TypeError, ValueError):
            return {"ok": False, "error": "weekdays must contain integers from 0 (Monday) through 6 (Sunday)"}
        manifest = self.registry.get(target_device_id.strip()) if self.registry.is_registered(target_device_id.strip()) else None
        if manifest is None:
            return {"ok": False, "error": "unknown target device"}
        if capability is None and service is not None:
            capability_descriptor = next(
                (item for item in manifest.capabilities if item.service == service and item.writable), None
            )
            if capability_descriptor is None:
                return {"ok": False, "error": "target device does not expose that writable service"}
            capability = capability_descriptor.name
        if not isinstance(capability, str) or not capability.strip():
            return {"ok": False, "error": "a writable device capability is required"}
        try:
            capability_descriptor = manifest.capability(capability.strip())
        except KeyError:
            return {"ok": False, "error": "unknown target device capability"}
        if not capability_descriptor.writable or not capability_descriptor.service:
            return {"ok": False, "error": "the selected capability is not writable"}
        if parameters is not None and not isinstance(parameters, Mapping):
            return {"ok": False, "error": "parameters must be an object"}
        if interpretation is not None and not isinstance(interpretation, str):
            return {"ok": False, "error": "interpretation must be a string or null"}
        normalized_parameters = parameters or {}
        try:
            action_parameters = tuple(normalized_parameters.items())
            draft = RuleDraft(
                draft_id=_new_id("draft"),
                household_id=self.household_id,
                proposed_by=self.resident.actor_id,
                source_text=source_text.strip(),
                interpretation=(interpretation or source_text).strip(),
                action_kind=ActionKind.UNSCOPED_EXECUTION,
                schedule_trigger=schedule,
                target_device_id=target_device_id.strip(),
                capability=capability_descriptor.name,
                parameters=action_parameters,
            )
            rule = self.runtime.propose_draft(draft, principal=self.resident, now=self._clock())
        except (TypeError, ValueError, KeyError) as exc:
            return {"ok": False, "error": str(exc)}
        self._persist_rules()
        self._publish_state()
        return {
            "ok": True,
            "automation": serialize.rule_to_dict(rule, device_room=manifest.room),
            "state": self.state(),
        }

    def update_automation(
        self,
        rule_id: str,
        *,
        source_text: str,
        time_of_day: str,
        weekdays: list[int] | tuple[int, ...] = (),
        interpretation: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        justification: str,
    ) -> dict[str, Any]:
        """Clarify a proposed automation without bypassing the rule ledger.

        A proposed rule is the editable authoring form. Once an owner has
        approved it, its meaning is immutable: the user must revoke it and
        create a new proposal. This keeps the approval attached to exactly
        the draft the owner reviewed.
        """

        if not self.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "automation editing requires a non-empty justification"}
        if not isinstance(source_text, str) or not source_text.strip():
            return {"ok": False, "error": "a non-empty 'source_text' is required"}
        try:
            rule = self.store.get_rule(rule_id)
        except KeyError:
            return {"ok": False, "error": "unknown automation"}
        if rule.status is not RuleStatus.PROPOSED:
            return {"ok": False, "error": "only proposed automations can be edited; revoke and add a new one"}
        try:
            parsed_time = dt_time.fromisoformat(str(time_of_day).strip())
            days = frozenset(weekdays)
            if any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6 for day in days):
                raise ValueError
            schedule = ScheduleTrigger(time_of_day=parsed_time, weekdays=days)
            normalized_parameters = rule.draft.parameters if parameters is None else tuple(parameters.items())
            if not isinstance(interpretation, (str, type(None))):
                raise ValueError("interpretation must be a string or null")
            draft = replace(
                rule.draft,
                draft_id=_new_id("draft"),
                source_text=source_text.strip(),
                interpretation=(interpretation or source_text).strip(),
                schedule_trigger=schedule,
                parameters=normalized_parameters,
            )
            result = self.runtime.clarify_rule(
                rule_id,
                draft,
                principal=self.resident,
                justification=justification,
                now=self._clock(),
            )
        except (TypeError, ValueError, KeyError) as exc:
            return {"ok": False, "error": str(exc)}
        self._persist_rules()
        self._publish_state()
        return {
            "ok": result.decision.status is DecisionStatus.ALLOW,
            "decision": {
                "status": result.decision.status.value,
                "code": result.decision.code.value,
                "explanation": result.decision.explanation,
            },
            "automation": serialize.rule_to_dict(
                result.rule,
                device_room=self._device_room(result.rule.draft.target_device_id)
                if result.rule.draft.target_device_id is not None
                else None,
            ),
            "state": self.state(),
        }

    def approve_automation(self, rule_id: str, *, justification: str) -> dict[str, Any]:
        if not self.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        try:
            result = self.runtime.approve_rule(
                rule_id,
                principal=self.owner,
                justification=justification,
                now=self._clock(),
            )
        except KeyError:
            return {"ok": False, "error": "unknown automation"}
        self._persist_rules()
        self._publish_state()
        return {
            "ok": result.decision.status is DecisionStatus.ALLOW,
            "decision": {
                "status": result.decision.status.value,
                "code": result.decision.code.value,
                "explanation": result.decision.explanation,
            },
            "automation": serialize.rule_to_dict(
                result.rule,
                device_room=self._device_room(result.rule.draft.target_device_id)
                if result.rule.draft.target_device_id is not None
                else None,
            ),
            "state": self.state(),
        }

    def revoke_automation(self, rule_id: str, *, justification: str) -> dict[str, Any]:
        if not self.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        try:
            result = self.runtime.revoke_rule(
                rule_id,
                principal=self.owner,
                justification=justification,
                now=self._clock(),
            )
        except KeyError:
            return {"ok": False, "error": "unknown automation"}
        self._persist_rules()
        self._publish_state()
        return {
            "ok": result.decision.status is DecisionStatus.ALLOW,
            "decision": {
                "status": result.decision.status.value,
                "code": result.decision.code.value,
                "explanation": result.decision.explanation,
            },
            "automation": serialize.rule_to_dict(
                result.rule,
                device_room=self._device_room(result.rule.draft.target_device_id)
                if result.rule.draft.target_device_id is not None
                else None,
            ),
            "state": self.state(),
        }

    def chat(self, text: str, focus: str | None = None) -> dict[str, Any]:
        self._say("user", text)
        self._chat_intent(text, focus=focus)
        self._publish_state()
        return self.state()

    # The services with their own closed ActionKind (garage open/close are
    # GUARDED, so the engine answers CONFIRMATION_REQUIRED and the normal
    # approve flow runs). Anything else falls through to capability-driven
    # routing in `device_command` -- this map is deliberately not the full
    # list of controllable services.
    _DEVICE_COMMAND_KINDS = {
        "light.turn_off": ActionKind.TURN_LIGHT_OFF,
        "light.set_brightness": ActionKind.SET_LIGHT_BRIGHTNESS,
        "cover.open": ActionKind.OPEN_GARAGE,
        "cover.close": ActionKind.CLOSE_GARAGE,
    }

    def device_command(
        self, device_id: str, service: str, parameters: Mapping | None = None
    ) -> dict[str, Any]:
        if not self.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not self.registry.is_registered(device_id):
            return {"ok": False, "error": "unknown device"}
        manifest = self.registry.get(device_id)
        action_kind = self._DEVICE_COMMAND_KINDS.get(service)
        capability_name: str | None = None
        if action_kind is None:
            # Not one of the four services with their own ActionKind: route
            # by whatever capability this device's own manifest declares
            # under that service instead of refusing outright. This is what
            # makes a switch's or fan's plain "power" commandable without a
            # dedicated ActionKind ever existing for it -- the control is
            # driven by DeviceManifest, not a hardcoded service list.
            capability = next(
                (cap for cap in manifest.capabilities if cap.service == service and cap.writable), None
            )
            if capability is None:
                return {"ok": False, "error": "unsupported service"}
            action_kind = ActionKind.UNSCOPED_EXECUTION
            capability_name = capability.name
        elif not any(capability.service == service for capability in manifest.capabilities):
            return {"ok": False, "error": "unsupported service"}
        proposal_parameters: tuple[tuple[str, Any], ...] = ()
        if action_kind is ActionKind.SET_LIGHT_BRIGHTNESS:
            brightness = (parameters or {}).get("brightness_pct")
            if isinstance(brightness, bool) or not isinstance(brightness, int):
                return {"ok": False, "error": "an integer 'brightness_pct' is required"}
            proposal_parameters = (("brightness_pct", brightness),)
        proposal = ActionProposal(
            action_kind=action_kind,
            capability=capability_name,
            target_device_id=device_id,
            target_selector=None,
            parameters=proposal_parameters,
            justification=f"device control: {service}",
            source_text=f"device control: {service}",
        )
        self._run_proposal(proposal)
        self._publish_state()
        return {"ok": True, "state": self.state()}

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
            # Answering a question is read-only -- it never reaches
            # authority or a device, so it stays available even before a
            # household has declared anyone.
            self._answer_query(intent, focus=focus)
        elif isinstance(intent, (ActionProposal, RuleDraft)) and not self.has_declared_owner:
            self._say("haven", NO_OWNER_DECLARED_LINE)
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
        if (
            normalized in ("close the garage", "close the garage door")
            and self.house is not None
            and not self.house.garage_open()
        ):
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
        self._persist_rules()

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
        garage_id = self._garage_device_id()
        if garage_id is not None and "garage" in normalized and ("open" in normalized or "closed" in normalized):
            device = next((item for item in view.devices if item.device_id == garage_id), None)
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

    def _garage_device_id(self) -> str | None:
        """The device id the deterministic floor's canned garage question
        recognizes. `None` on the production controller: a real household's
        garage cover can be named anything, and there is no household-wide
        "the garage" alias yet -- `DemoDirector` knows its own fixture id."""

        return None

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
            self.world.observe(now),
            now=now,
            device_registry=self.registry,
            names=self._world_names(),
            recent_events=self.store.events,
        )

    def _agent_context(self, *, focus: str | None, now: datetime) -> AgentContext:
        return AgentContext(
            household_id=self.household_id,
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
                capability=proposal.capability,
                target_device_id=proposal.target_device_id,
                target_selector=proposal.target_selector,
                parameters=proposal.parameters,
                justification=proposal.justification,
                world=self.world.observe(now),
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
            if self.house is not None:
                detail = (
                    f"It has been open {self.house.garage_open_minutes(at=now)} minutes. "
                    "You asked to close it directly; this action needs your confirmation."
                )
            else:
                detail = "You asked to close it directly; this action needs your confirmation."
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
        for item in self.world.observe(self._clock()).presence:
            if item.present:
                return item.room_id
        return None

    def voice_wake(self) -> dict[str, Any]:
        if not self.voice_enabled:
            self._say("haven", "Voice control is disabled.")
            return {"ok": False, "state": self.state()}
        ok = self.voice.wake()
        return {"ok": ok, "state": self.state()}

    def voice_utterance(self, text: str) -> dict[str, Any]:
        if not self.voice_enabled:
            self._say("haven", "Voice control is disabled.")
            return {"ok": False, "state": self.state()}
        ok = self.voice.utterance(text)
        return {"ok": ok, "state": self.state()}

    def voice_cancel(self) -> dict[str, Any]:
        ok = self.voice.cancel()
        return {"ok": ok, "state": self.state()}

    def reset(self) -> dict[str, Any]:
        # Resetting a scripted scenario is demo-only; a live world is not
        # ours to reset.
        return {"ok": False, "state": self.state()}

    def state(self) -> dict[str, Any]:
        now = self._clock()
        world = self.world.observe(now)
        present = {item.person_id: item.room_id for item in world.presence if item.present}
        rooms = [
            {"id": room_id, "devices": [], "people": [], "camera": None}
            for room_id in self._room_names
        ]
        for room_id in present.values():
            if not any(existing["id"] == room_id for existing in rooms):
                rooms.append({"id": room_id, "devices": [], "people": [], "camera": None})
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
                    motion=self.house.motion_detected() if self.house is not None else False,
                    online=camera_state is not None and camera_state.status != EvidenceStatus.UNAVAILABLE,
                )
        for room in rooms:
            room["people"] = sorted(
                self._person_name(person_id) for person_id, room_id in present.items() if room_id == room["id"]
            )
        rooms_payload = [
            serialize.room_to_dict(
                room_id=room["id"],
                name=self._room_names.get(room["id"], room["id"].replace("_", " ").title()),
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
        if self._glow == GLOW_CRITICAL and self.house is not None and self.house.camera_down():
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

    def _person_name(self, person_id: str) -> str:
        declared = self._person_names.get(person_id)
        if declared is not None:
            return declared
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
        if sender == "haven":
            self._speak_aloud(text)

    def _speak_aloud(self, text: str) -> None:
        """Play `text` through whatever real TTS path is available, if any.

        Real playback runs in real time, so this always hands off to a
        background thread -- callers here are synchronous HTTP handlers
        (chat/approve/device_command) that must return promptly regardless
        of how long the household's speaker takes to finish a sentence.
        Prefers the full `SpeechService` pump when it's running (keeps
        `VoiceSession` state -- SPEAKING glow, barge-in -- in sync with
        real audio); falls back to the standalone synthesizer+sink pair
        (`start_voice()`'s `_start_tts_playback`) when only TTS output is
        wired, matching a household whose wake/ASR input comes from
        elsewhere entirely. Neither being available is a documented no-op.
        """

        service = self._speech_service
        if service is not None:
            threading.Thread(target=service.say, args=(text,), daemon=True).start()
            return
        synthesizer = self._tts_synthesizer
        sink = self._tts_sink
        if synthesizer is None or sink is None:
            return

        def _play() -> None:
            try:
                sink.play(synthesizer.speak(text))
            except Exception:
                pass

        threading.Thread(target=_play, daemon=True).start()

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
        self._persist_history()
        self._publish("state", self.state())

    def _persist_history(self) -> None:
        """Durably record whatever is new since the last publish.

        Called on every `_publish_state()`, the single choke point nearly
        every governed flow already reaches -- so this needs no bookkeeping
        at each of chat/approve/deny/device_command/scheduler-tick/etc.
        individually. Events, actions, and receipts written by this store
        this run are the only ones this durably records; nothing here
        fabricates history for a transition that did not actually happen.
        """

        if self._history is None:
            return
        for event in self.store.events[self._persisted_event_count :]:
            self._history.append_event(event)
        self._persisted_event_count = len(self.store.events)
        for entry in self.store.state.memory[self._persisted_memory_count :]:
            self._history.append_memory(entry)
        self._persisted_memory_count = len(self.store.state.memory)
        for action in self.store.state.actions:
            self._history.save_action(action)
        for receipt in self.receipts[self._persisted_receipt_count :]:
            self._history.append_receipt(receipt)
        self._persisted_receipt_count = len(self.receipts)

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
    "ACTIVITY_LIMIT",
    "ALREADY_EXECUTING_LINE",
    "ATTENTION_LINE",
    "CAMERA_BLIND_LINE",
    "Clock",
    "GLOW_ACTING",
    "GLOW_COMPLETED",
    "GLOW_CRITICAL",
    "GLOW_IDLE",
    "GLOW_PERMISSION",
    "HavenApplication",
    "MEMORY_LIMIT",
    "NO_OWNER_DECLARED_LINE",
    "PendingRequest",
    "STOPPED_LINE",
    "STOP_UTTERANCES",
    "VOICE_ACK_SECONDS",
    "VOICE_REFRACTORY_SECONDS",
    "VoiceSession",
]
