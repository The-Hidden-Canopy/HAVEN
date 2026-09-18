"""Direct (human-initiated) actions: a member's command is the authorization.

A direct action executes with no rule proposed, approved, or stored; the
request's `origin` field (ActionOrigin.DIRECT, never a rule_id string)
marks the receipt's provenance, and the store's event log shows the same
event types as the rule path (ACTION_AUTHORIZED / ACTION_EXECUTED, or
ACTION_BLOCKED).
"""

from datetime import timedelta

import pytest

from haven.authority.policy import AuthorityEngine
from haven.core.domain import (
    ActionKind,
    ActionOrigin,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    AuthorityDecision,
    ChangeOrigin,
    ConfirmationToken,
    ContextState,
    DecisionCode,
    DecisionStatus,
    DeviceSelector,
    DeviceState,
    EventType,
    PresenceState,
    RoleTier,
    Rule,
    Transition,
    TransitionKind,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest, DeviceRegistry
from haven.errors import InvalidTransition
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import AmbiguousTargetError, HavenRuntime
from test_vertical_slice import BASE_TIME, _explicit_draft, _principal


def _runtime(registry: DeviceRegistry | None = None):
    principal = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=principal.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
        authority=AuthorityEngine(device_registry=registry),
    )
    return runtime, store, adapter, principal, owner


def _direct_world(principal) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="direct-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=30),
    )


def _human_touched_world(principal, *, now) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="override-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(hours=6),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id="bedroom",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.presence",
            ),
        ),
        contexts=(
            ContextState(
                context_id="working_late",
                active=True,
                observed_at=BASE_TIME,
                source="fixture.context",
            ),
        ),
        devices=(
            DeviceState(
                device_id="bedroom_lights",
                kind="light",
                room_id="bedroom",
                is_on=True,
                brightness_pct=100,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
                changed_by=ChangeOrigin.HUMAN,
            ),
        ),
    )


def _two_light_registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="bedroom_lamp",
            device_type="light",
            provider_id="fixture",
            room="bedroom",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, readable=True, writable=True, service="light.toggle"
                ),
            ),
        )
    )
    registry.register(
        DeviceManifest(
            device_id="kitchen_light",
            device_type="light",
            provider_id="fixture",
            room="kitchen",
            capabilities=(
                CapabilityDescriptor(
                    name="power", control_class=ControlClass.LOW_RISK, readable=True, writable=True, service="light.toggle"
                ),
            ),
        )
    )
    return registry


def test_member_direct_safe_action_executes_without_creating_a_rule() -> None:
    runtime, store, adapter, resident, owner = _runtime()

    receipt = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        justification="I am going to bed; turn the bedroom lights off right now.",
        world=_direct_world(resident),
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert receipt.decision.code.value == "allowed"
    assert receipt.requested_action.origin is ActionOrigin.DIRECT
    assert receipt.requested_action.rule_id == receipt.requested_action.request_id
    assert receipt.to_dict()["requested_action"]["origin"] == "direct"
    assert receipt.interpretation == "I am going to bed; turn the bedroom lights off right now."
    assert receipt.evidence == ()
    # no rule entered the store for a direct action
    assert store.state.rules == ()
    assert store.state.actions[0].status == ActionStatus.EXECUTED
    assert len(adapter.commands) == 1
    assert adapter.commands[0].service == "light.turn_off"
    # same recording discipline, same event types as the rule path
    assert [event.event_type for event in store.events] == [
        EventType.ACTION_AUTHORIZED,
        EventType.ACTION_EXECUTED,
    ]
    assert tuple(event.event_id for event in store.events) == receipt.event_ids


def test_direct_action_with_capability_routes_service_from_the_manifest() -> None:
    """A direct command for a device kind with no dedicated ActionKind.

    A switch or fan's plain "power" has no `ActionKind` of its own; routing
    by `capability` instead resolves both risk (from the capability's own
    `ControlClass`, bypassing the closed `ActionKind` set entirely) and the
    provider `service` from the device's manifest -- the direct-command
    analogue of a capability-routed `RuleDraft`.
    """

    registry = DeviceRegistry()
    registry.register(
        DeviceManifest(
            device_id="office_fan",
            device_type="fan",
            provider_id="home_assistant",
            room="office",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="fan.turn_off"),
            ),
        )
    )
    runtime, store, adapter, resident, owner = _runtime(registry)

    receipt = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.UNSCOPED_EXECUTION,
        capability="power",
        target_device_id="office_fan",
        justification="turning off the office fan",
        world=_direct_world(resident),
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert receipt.decision.code.value == "allowed"
    assert receipt.requested_action.capability == "power"
    assert len(adapter.commands) == 1
    assert adapter.commands[0].service == "fan.turn_off"


def test_direct_action_requires_exactly_one_target() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    with pytest.raises(ValueError, match="exactly one of target_device_id or target_selector"):
        runtime.run_action(
            principal=resident,
            action_kind=ActionKind.TURN_LIGHT_OFF,
            justification="Turn it off.",
            world=_direct_world(resident),
            now=BASE_TIME,
        )
    with pytest.raises(ValueError, match="exactly one of target_device_id or target_selector"):
        runtime.run_action(
            principal=resident,
            action_kind=ActionKind.TURN_LIGHT_OFF,
            target_device_id="bedroom_lights",
            target_selector=DeviceSelector(room="bedroom"),
            justification="Turn it off.",
            world=_direct_world(resident),
            now=BASE_TIME,
        )
    assert store.events == ()
    assert adapter.commands == ()


def test_direct_action_selector_resolves_a_single_match() -> None:
    runtime, store, adapter, resident, owner = _runtime(registry=_two_light_registry())

    receipt = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_selector=DeviceSelector(room="bedroom"),
        justification="Turn off the bedroom lamp.",
        world=_direct_world(resident),
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert receipt.requested_action.target_device_id == "bedroom_lamp"


def test_direct_action_selector_with_no_match_raises() -> None:
    runtime, store, adapter, resident, owner = _runtime(registry=_two_light_registry())

    with pytest.raises(ValueError, match="no registered device matched"):
        runtime.run_action(
            principal=resident,
            action_kind=ActionKind.TURN_LIGHT_OFF,
            target_selector=DeviceSelector(room="attic"),
            justification="Turn off the attic light.",
            world=_direct_world(resident),
            now=BASE_TIME + timedelta(minutes=2),
        )
    assert store.events == ()
    assert adapter.commands == ()


def test_direct_action_selector_with_multiple_matches_needs_clarification() -> None:
    runtime, store, adapter, resident, owner = _runtime(registry=_two_light_registry())

    with pytest.raises(AmbiguousTargetError, match="2 devices"):
        runtime.run_action(
            principal=resident,
            action_kind=ActionKind.TURN_LIGHT_OFF,
            target_selector=DeviceSelector(device_type="light"),
            justification="Turn off the lights.",
            world=_direct_world(resident),
            now=BASE_TIME + timedelta(minutes=2),
        )
    assert store.events == ()
    assert adapter.commands == ()


def test_confirmation_required_direct_action_then_token_allows() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)

    blocked = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.OPEN_GARAGE,
        target_device_id="garage",
        justification="I am leaving; open the garage now.",
        world=_direct_world(resident),
        now=now,
    )

    assert blocked.decision.status == DecisionStatus.CONFIRMATION_REQUIRED
    assert blocked.decision.code.value == "confirmation_required"
    assert blocked.outcome == "confirmation_required"
    assert adapter.commands == ()
    assert [event.event_type for event in store.events] == [EventType.ACTION_BLOCKED]

    token = ConfirmationToken(
        token_id="direct-confirmation-1",
        household_id=resident.household_id,
        rule_id=blocked.requested_action.rule_id,
        request_id=blocked.requested_action.request_id,
        confirmed_by=resident.actor_id,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    allowed = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.OPEN_GARAGE,
        target_device_id="garage",
        justification="I am leaving; open the garage now, confirmed.",
        world=_direct_world(resident),
        now=now + timedelta(minutes=1),
        confirmation_token=token,
    )

    assert allowed.outcome == "executed"
    assert allowed.decision.code.value == "allowed"
    assert allowed.requested_action.request_id == blocked.requested_action.request_id
    assert adapter.commands[0].service == "cover.open_cover"
    assert [event.event_type for event in store.events] == [
        EventType.ACTION_BLOCKED,
        EventType.ACTION_AUTHORIZED,
        EventType.ACTION_EXECUTED,
    ]

    reused = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.OPEN_GARAGE,
        target_device_id="garage",
        justification="Open it again.",
        world=_direct_world(resident),
        now=now + timedelta(minutes=2),
        confirmation_token=token,
    )
    assert reused.decision.code.value == "confirmation_reused"
    assert store.state.rules == ()


def test_forbidden_direct_action_denies() -> None:
    runtime, store, adapter, resident, owner = _runtime()

    receipt = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.UNSCOPED_EXECUTION,
        target_device_id="bedroom_lights",
        justification="Run whatever you think is best.",
        world=_direct_world(resident),
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.status == DecisionStatus.DENY
    assert receipt.decision.code.value == "forbidden_action"
    assert adapter.commands == ()
    assert [event.event_type for event in store.events] == [EventType.ACTION_BLOCKED]
    assert store.state.rules == ()


def test_guest_direct_action_is_denied() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    guest = _principal(actor_id="visitor-1", role=RoleTier.GUEST)

    receipt = runtime.run_action(
        principal=guest,
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        justification="Turn that off please.",
        world=_direct_world(guest),
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "wrong_role_tier"
    assert receipt.decision.required_role == RoleTier.MEMBER
    assert adapter.commands == ()


def test_direct_action_requires_a_justification() -> None:
    runtime, store, adapter, resident, owner = _runtime()

    receipt = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        justification="   ",
        world=_direct_world(resident),
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "missing_justification"
    assert adapter.commands == ()


def test_recent_human_change_blocks_rule_but_not_direct_action() -> None:
    # A member directly touching a device suspends automation for the
    # override window -- but cannot block the human's own new command.
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=30)
    world = _human_touched_world(resident, now=now)

    direct = runtime.run_action(
        principal=resident,
        action_kind=ActionKind.SET_LIGHT_BRIGHTNESS,
        target_device_id="bedroom_lights",
        parameters=(("brightness_pct", 20),),
        justification="I want the lights at 20% right now, regardless of the rule.",
        world=world,
        now=now,
    )
    assert direct.outcome == "executed"
    assert direct.decision.code.value == "allowed"

    rule = runtime.propose_draft(_explicit_draft(resident), principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the bedroom light cap.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    rule_receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=world,
        justification="Apply the approved cap.",
        now=now,
    )
    assert rule_receipt.decision.code.value == "human_override_active"

    assert store.state.rules != ()  # the rule path created its rule
    assert len(adapter.commands) == 1  # only the direct action executed


def test_unknown_capability_fails_closed_for_direct_actions() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)
    request = ActionRequest(
        request_id="request-direct-1",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id="request-direct-1",
        action_kind=ActionKind.SET_THERMOSTAT,
        target_device_id="bedroom_lights",
        parameters=(("temperature", 21),),
        justification="Set the thermostat.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
        origin=ActionOrigin.DIRECT,
        capability="self_destruct",
    )

    decision = runtime.authority.decide_direct(
        request, principal=resident, world=_direct_world(resident), now=now
    )

    assert decision.status == DecisionStatus.DENY
    assert decision.code.value == "unknown_capability"


def test_origin_is_typed_and_coerced_from_str() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)

    coerced = ActionRequest(
        request_id="request-direct-coerce",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id="request-direct-coerce",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        parameters=(),
        justification="Turn it off.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
        origin="direct",
    )
    assert coerced.origin is ActionOrigin.DIRECT

    default_origin = ActionRequest(
        request_id="request-rule-default",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id="rule-1",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        parameters=(),
        justification="Apply the rule.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
    )
    assert default_origin.origin is ActionOrigin.RULE

    with pytest.raises(ValueError):
        ActionRequest(
            request_id="request-bad-origin",
            household_id=resident.household_id,
            requested_by=resident.actor_id,
            rule_id="rule-1",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            target_device_id="bedroom_lights",
            parameters=(),
            justification="Apply the rule.",
            evidence_snapshot_id="direct-snapshot",
            requested_at=now,
            origin="automation",
        )


def test_decide_direct_rejects_rule_origin_requests() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)
    rule = Rule(rule_id="rule-1", draft=_explicit_draft(resident))
    request = ActionRequest(
        request_id="request-rule-1",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id=rule.rule_id,
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        parameters=(),
        justification="Apply the rule.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
        origin=ActionOrigin.RULE,
    )

    with pytest.raises(ValueError, match=r"decide_direct\(\) only evaluates direct-origin"):
        runtime.authority.decide_direct(request, principal=resident, world=_direct_world(resident), now=now)


def test_decide_rejects_direct_origin_requests() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)
    rule = Rule(rule_id="rule-1", draft=_explicit_draft(resident))
    request = ActionRequest(
        request_id="request-direct-1",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id="request-direct-1",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        parameters=(),
        justification="Turn it off now.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
        origin=ActionOrigin.DIRECT,
    )

    with pytest.raises(ValueError, match=r"decide\(\) only evaluates rule-origin"):
        runtime.authority.decide(
            request, principal=resident, rule=rule, world=_direct_world(resident), now=now
        )


def _authorized_record(request: ActionRequest) -> ActionRecord:
    return ActionRecord(
        action_id=f"action-{request.request_id}",
        request=request,
        status=ActionStatus.AUTHORIZED,
        decision=AuthorityDecision(DecisionStatus.ALLOW, DecisionCode.ALLOWED, "allowed by fixture"),
    )


def _authorize(store: HavenStore, record: ActionRecord, *, actor_id: str, now) -> None:
    store.execute_transition(
        Transition(
            kind=TransitionKind.AUTHORIZE_ACTION,
            household_id=store.household_id,
            actor_id=actor_id,
            payload=record,
            correlation_id=record.request.request_id,
        ),
        now=now,
    )


def test_store_authorize_gate_skips_the_rule_requirement_for_direct_origin() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)
    request = ActionRequest(
        request_id="request-direct-gate",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id="request-direct-gate",  # no stored rule has this id; none is needed
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        parameters=(),
        justification="Turn it off now.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
        origin=ActionOrigin.DIRECT,
    )

    _authorize(store, _authorized_record(request), actor_id=resident.actor_id, now=now)

    assert store.state.rules == ()  # still no rule in the store for a direct action
    assert len(store.state.actions) == 1
    assert store.state.actions[0].request.origin is ActionOrigin.DIRECT


def test_store_authorize_gate_still_fails_closed_for_unknown_rule_ids() -> None:
    runtime, store, adapter, resident, owner = _runtime()
    now = BASE_TIME + timedelta(minutes=2)
    request = ActionRequest(
        request_id="request-rule-gate",
        household_id=resident.household_id,
        requested_by=resident.actor_id,
        rule_id="rule-not-in-store",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="bedroom_lights",
        parameters=(),
        justification="Apply the rule.",
        evidence_snapshot_id="direct-snapshot",
        requested_at=now,
        origin=ActionOrigin.RULE,
    )

    with pytest.raises(InvalidTransition, match="an action requires an approved rule"):
        _authorize(store, _authorized_record(request), actor_id=resident.actor_id, now=now)

    assert store.state.actions == ()
