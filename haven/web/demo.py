"""The local fixture household: SimulatedHouse and DemoDirector.

`HavenApplication` (`haven.web.haven_application`) is the production
controller -- chat, permissions, voice, scheduling, state projection,
persistence -- shared by every real household. This module adds exactly
what the local demo needs on top of it: a mutable in-memory house, its own
registry of five fixture devices, a scripted three-rule scenario, and the
`gerron` fixture identity. `DemoDirector` is the thin subclass; nothing in
`HavenApplication` imports anything from this file.
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any

from haven.core.domain import (
    ActionKind,
    ChangeOrigin,
    ContextState,
    DecisionStatus,
    DeviceState,
    DeviceResult,
    EvidenceStatus,
    PresenceState,
    Principal,
    RoleTier,
    RuleDraft,
    ScheduleTrigger,
    WorldSnapshot,
)
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.execution import ExecutionProviderRegistry

from .haven_application import (
    ALREADY_EXECUTING_LINE,
    ATTENTION_LINE,
    CAMERA_BLIND_LINE,
    Clock,
    GLOW_ACTING,
    GLOW_COMPLETED,
    GLOW_CRITICAL,
    GLOW_IDLE,
    GLOW_PERMISSION,
    HavenApplication,
    MEMORY_LIMIT,
    NO_OWNER_DECLARED_LINE,
    PendingRequest,
    STOPPED_LINE,
    STOP_UTTERANCES,
    VOICE_ACK_SECONDS,
    VOICE_REFRACTORY_SECONDS,
    VoiceSession,
    _new_id,
)
from .history_persist import HistoryStore
from .rules_persist import RulesPersistence

HOUSEHOLD_ID = "household-demo"
PERSON_ID = "gerron"
PROVIDER_ID = "simulated_house"

DRIVEWAY_CAM_ID = "driveway_cam"
DRIVEWAY_ROOM = "driveway"


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
        # The runtime's ActionKind mapping sends "cover.open_cover" /
        # "light.turn_on" for direct open/brightness actions; both names land
        # on the same simulated behavior.
        if command.service in ("cover.open", "cover.open_cover"):
            if device["kind"] != "cover":
                return DeviceResult(False, "cover.open on a non-cover device", command.requested_at, "demo.house")
            device["open"] = True
            device["is_on"] = True
            device["observed_at"] = command.requested_at
            device["changed_by"] = ChangeOrigin.SYSTEM
            return DeviceResult(True, "Garage door opened.", command.requested_at, "demo.house")
        if command.service in ("light.set_brightness", "light.turn_on"):
            pct = max(0, min(100, int(dict(command.parameters).get("brightness_pct", 100))))
            device["brightness_pct"] = pct
            device["is_on"] = pct > 0
            device["observed_at"] = command.requested_at
            device["changed_by"] = ChangeOrigin.SYSTEM
            return DeviceResult(True, f"Brightness set to {pct}%.", command.requested_at, "demo.house")
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


class SimulatedWorldProvider:
    """WorldProvider over a SimulatedHouse: the demo world's observe call."""

    def __init__(self, house: SimulatedHouse) -> None:
        self.house = house

    def observe(self, now: datetime) -> WorldSnapshot:
        return self.house.snapshot(now)


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


class DemoDirector(HavenApplication):
    """The local fixture household: a SimulatedHouse plus a scripted scenario.

    Adds exactly three things `HavenApplication` doesn't have: a mutable
    in-memory house (and its own five-device registry/execution adapter),
    the `gerron` fixture identity, and the garage/camera/office-light demo
    scenario. Everything else -- chat, permissions, voice, scheduling, state
    projection -- is inherited unchanged.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        voice_ack_seconds: float = VOICE_ACK_SECONDS,
        voice_refractory_seconds: float = VOICE_REFRACTORY_SECONDS,
        capability_registry=None,
        model_manager=None,
        scheduler_tick_seconds: float = 20.0,
        scenario: bool = True,
        voice_enabled: bool = True,
        intelligence_provider=None,
        rules_persistence: RulesPersistence | None = None,
        resident: Principal | None = None,
        owner: Principal | None = None,
        household_id: str | None = None,
        history: HistoryStore | None = None,
    ) -> None:
        # Read by the overridden `_seed_initial_rules`, called from inside
        # `super().__init__()` before this method gets to do anything else.
        self._scenario_enabled = scenario
        clock = clock or (lambda: datetime.now(timezone.utc))
        now = clock()
        # Set before `super().__init__()`: `_seed_initial_rules`/`start_scenario`
        # dispatch to this class's overrides during that call and need the
        # house and adapter to already exist.
        house = SimulatedHouse(now=now)
        self.house = house
        adapter = SimulatedExecutionAdapter(house)
        self.adapter = adapter
        world = SimulatedWorldProvider(house)
        registry = build_registry()
        execution = ExecutionProviderRegistry()
        execution.register(PROVIDER_ID, adapter)
        resolved_household_id = household_id if household_id is not None else HOUSEHOLD_ID
        resolved_resident = (
            resident
            if resident is not None
            else Principal(actor_id=PERSON_ID, household_id=resolved_household_id, role_tier=RoleTier.MEMBER)
        )
        resolved_owner = (
            owner
            if owner is not None
            else Principal(actor_id=PERSON_ID, household_id=resolved_household_id, role_tier=RoleTier.OWNER)
        )
        super().__init__(
            clock=clock,
            voice_ack_seconds=voice_ack_seconds,
            voice_refractory_seconds=voice_refractory_seconds,
            capability_registry=capability_registry,
            model_manager=model_manager,
            scheduler_tick_seconds=scheduler_tick_seconds,
            world=world,
            registry=registry,
            execution=execution,
            voice_enabled=voice_enabled,
            intelligence_provider=intelligence_provider,
            rules_persistence=rules_persistence,
            resident=resolved_resident,
            owner=resolved_owner,
            household_id=resolved_household_id,
            history=history,
        )

    def _seed_initial_rules(self, now: datetime) -> None:
        if self._scenario_enabled:
            self.garage_rule_id = self._prepare_garage_rule(now)
            self.camera_rule_id = self._prepare_camera_rule(now)
            self.set_scheduler_enabled(self.garage_rule_id, False)
            self.set_scheduler_enabled(self.camera_rule_id, False)
            self.office_light_rule_id = self._prepare_office_light_rule(now)
            self.start_scenario()
        else:
            self.garage_rule_id = None
            self.camera_rule_id = None
            self.office_light_rule_id = None

    def _garage_device_id(self) -> str | None:
        return "garage_door"

    def _prepare_garage_rule(self, now: datetime) -> str:
        draft = RuleDraft(
            draft_id="draft-garage-close",
            household_id=self.household_id,
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
        self._persist_rules()
        return rule.rule_id

    def _prepare_camera_rule(self, now: datetime) -> str:
        # The rule's trigger evaluation reads the target device's own evidence
        # (WorldSnapshot.evaluate_trigger ends with an evidence_problem check on
        # the target device), so a driveway camera marked UNAVAILABLE makes
        # AuthorityEngine.decide() return UNAVAILABLE through the real path.
        draft = RuleDraft(
            draft_id="draft-driveway-clip",
            household_id=self.household_id,
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
        self._persist_rules()
        return rule.rule_id

    OFFICE_LIGHT_OFF_TIME = dt_time(22, 35)

    def _prepare_office_light_rule(self, now: datetime) -> str:
        # The demo's one real schedule: a SAFE_AUTOMATIC light-off at 22:35
        # every day, observable (the office light state flips) and harmless.
        draft = RuleDraft(
            draft_id="draft-office-light-off",
            household_id=self.household_id,
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
        self._persist_rules()
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
            world=self.world.observe(now),
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

    def mark_camera_down(self) -> dict[str, Any]:
        self.house.set_camera_down(True)
        now = self._clock()
        receipt = self.runtime.run_rule(
            self.camera_rule_id,
            principal=self.resident,
            world=self.world.observe(now),
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
            world=self.world.observe(now),
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
        # Post-reset state is authoritative: make the sidecar say so too.
        self._persist_rules()
        if scheduler_running:
            self.start_scheduler()
        return self.state()


__all__ = [
    "ALREADY_EXECUTING_LINE",
    "ATTENTION_LINE",
    "CAMERA_BLIND_LINE",
    "Clock",
    "DemoDirector",
    "GLOW_ACTING",
    "GLOW_COMPLETED",
    "GLOW_CRITICAL",
    "GLOW_IDLE",
    "GLOW_PERMISSION",
    "HOUSEHOLD_ID",
    "MEMORY_LIMIT",
    "NO_OWNER_DECLARED_LINE",
    "PROVIDER_ID",
    "PendingRequest",
    "STOPPED_LINE",
    "STOP_UTTERANCES",
    "SimulatedExecutionAdapter",
    "SimulatedHouse",
    "SimulatedWorldProvider",
    "VoiceSession",
    "build_registry",
]
