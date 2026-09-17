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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ChangeOrigin,
    ConfirmationToken,
    ContextState,
    DecisionStatus,
    DeviceSelector,
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
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.execution import ExecutionProviderRegistry
from haven.intelligence.gateway import FixtureModelGateway
from haven.runtime import HavenRuntime

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


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    rule_id: str
    title: str
    detail: str
    expires_at: datetime


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

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        now = self._clock()
        self.house = SimulatedHouse(now=now)
        self.registry = build_registry()
        self.adapter = SimulatedExecutionAdapter(self.house)
        providers = ExecutionProviderRegistry()
        providers.register(PROVIDER_ID, self.adapter)
        self.store = HavenStore(household_id=HOUSEHOLD_ID)
        self.runtime = HavenRuntime(
            store=self.store,
            model_gateway=FixtureModelGateway(),
            authority=AuthorityEngine(device_registry=self.registry),
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
        self._auto_allow: set[str] = set()
        self.receipts: list = []
        self.garage_rule_id = self._prepare_garage_rule(now)
        self.camera_rule_id = self._prepare_camera_rule(now)
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

    def _schedule_trigger(self, now: datetime) -> ScheduleTrigger:
        return ScheduleTrigger(time_of_day=now.time().replace(microsecond=0), window=timedelta(hours=24))

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

    def deny(self, request_id: str) -> bool:
        if request_id not in self._pending:
            return False
        del self._pending[request_id]
        self._say("user", "No, leave it.")
        self._say("haven", "Understood — I'll leave the garage as it is.")
        self._set_glow(GLOW_IDLE)
        self._publish_state()
        return True

    def chat(self, text: str, focus: str | None = None) -> dict[str, Any]:
        self._say("user", text)
        normalized = " ".join(text.casefold().split()).rstrip(".!?")
        if normalized in ("close the garage", "close the garage door"):
            self._chat_close_garage()
        elif normalized in ("turn that light off", "turn off the light", "turn off that light"):
            if focus is None:
                self._say("haven", "Which room do you mean?")
            else:
                self._chat_light_off(focus)
        else:
            self._say("haven", "I can't do that yet.")
        self._publish_state()
        return self.state()

    def _chat_close_garage(self) -> None:
        now = self._clock()
        if not self.house.garage_open():
            self._say("haven", "The garage is already closed.")
            return
        receipt = self.runtime.run_rule(
            self.garage_rule_id,
            principal=self.resident,
            world=self.house.snapshot(now),
            justification="The resident asked HAVEN to close the garage.",
            now=now,
        )
        self.receipts.append(receipt)
        if receipt.decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._ask_garage_close(now, self.house.garage_open_minutes(at=now))
        elif receipt.decision.status == DecisionStatus.ALLOW:
            self._say("haven", "The garage door is closed.")
            self._set_glow(GLOW_COMPLETED, target="garage")
        else:
            self._record_block(receipt)

    def _chat_light_off(self, focus: str) -> None:
        now = self._clock()
        if not self.registry.find(role="light", room=focus):
            self._say("haven", f"I don't see a light in the {focus}.")
            return
        draft = RuleDraft(
            draft_id=_new_id("draft"),
            household_id=HOUSEHOLD_ID,
            proposed_by=self.resident.actor_id,
            source_text="turn that light off",
            interpretation=f"Turn off the {focus} light when the resident asks.",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            schedule_trigger=self._schedule_trigger(now),
            capability="power",
            target_selector=DeviceSelector(role="light", room=focus),
        )
        rule = self.runtime.propose_draft(draft, principal=self.resident, now=now)
        self.runtime.approve_rule(
            rule.rule_id,
            principal=self.owner,
            justification=f"Owner approved turning off the {focus} light on request.",
            now=now,
        )
        self._set_glow(GLOW_ACTING)
        receipts = self.runtime.run_rule_for_group(
            rule.rule_id,
            principal=self.resident,
            world=self.house.snapshot(now),
            justification=f"Turn off the {focus} light.",
            now=now,
        )
        self.receipts.extend(receipts)
        if receipts and all(receipt.decision.status == DecisionStatus.ALLOW for receipt in receipts):
            self._say("haven", f"Done — the {focus} light is off.")
            self._set_glow(GLOW_COMPLETED)
        else:
            for receipt in receipts:
                if receipt.decision.status != DecisionStatus.ALLOW:
                    self._record_block(receipt)

    def reset(self) -> dict[str, Any]:
        self._pending.clear()
        self._conversation.clear()
        self._auto_allow.clear()
        self._glow = GLOW_IDLE
        self.start_scenario()
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
                room["camera"] = serialize.camera_to_dict(
                    camera_id=manifest.device_id,
                    label=manifest.device_id.removesuffix("_cam").removesuffix("_camera").replace("_", " ").title(),
                    motion=self.house.motion_detected(),
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
        line = ATTENTION_LINE if (self._pending or self._glow in (GLOW_PERMISSION, GLOW_CRITICAL)) else NOMINAL_LINE
        return serialize.state_to_dict(
            glow=self._glow,
            revision=self.store.state.revision,
            rooms=rooms_payload,
            contexts=contexts,
            people=people,
            pending=[serialize.pending_to_dict(item) for item in self._pending.values()],
            conversation=[serialize.message_to_dict(sender=m["from"], text=m["text"]) for m in self._conversation],
            status=serialize.status_to_dict(
                devices=len(tuple(self.registry.all_devices())), people=len(people), line=line
            ),
        )

    @staticmethod
    def _person_name(person_id: str) -> str:
        return person_id.replace("_", " ").title()

    def _record_block(self, receipt) -> None:
        self._say("haven", f"I couldn't do that: {receipt.decision.explanation}")
        self._set_glow(GLOW_CRITICAL)

    def _say(self, sender: str, text: str) -> None:
        self._conversation.append({"from": sender, "text": text})

    def _set_glow(self, glow: str) -> None:
        if glow == self._glow:
            return
        self._glow = glow
        self._publish("glow", {"state": glow})

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
    "build_registry",
]
