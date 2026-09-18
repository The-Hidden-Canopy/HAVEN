"""Serialization contract for the web surface: ISO datetimes, enum values,
RoleTier names, and lists (never tuples).
"""

from datetime import datetime, timedelta, timezone

from haven.core.domain import (
    ActionKind,
    CoverState,
    DecisionStatus,
    DeviceSelector,
    DeviceState,
    DomainEvent,
    EventType,
    EvidenceStatus,
    MemoryEntry,
    RoleTier,
    Rule,
    RuleDraft,
    RuleStatus,
    ScheduleTrigger,
)
from haven.providers import ProviderCapabilities
from haven.web import serialize
from haven.web.demo import PendingRequest

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _device() -> DeviceState:
    return DeviceState(
        device_id="office_light",
        kind="light",
        room_id="office",
        is_on=True,
        brightness_pct=70,
        observed_at=NOW,
        source="demo.house",
    )


def test_device_to_dict_surfaces_cover_state_never_collapsing_open_to_none() -> None:
    garage = DeviceState(
        device_id="cover.garage_door",
        kind="cover",
        room_id="garage",
        is_on=True,
        brightness_pct=None,
        observed_at=NOW,
        source="home_assistant.rest",
        raw_state="open",
        cover_state=CoverState.OPEN,
    )
    payload = serialize.device_to_dict(garage, role="cover")
    assert payload["raw_state"] == "open"
    assert payload["cover_state"] == "open"
    assert payload["is_on"] is True


def test_to_json_value_maps_enums_datetimes_tuples_and_role_tiers() -> None:
    assert serialize.to_json_value(DecisionStatus.ALLOW) == "allow"
    assert serialize.to_json_value(NOW) == NOW.isoformat()
    assert isinstance(serialize.to_json_value((1, 2)), list)
    assert serialize.to_json_value((1, 2)) == [1, 2]
    assert serialize.to_json_value(RoleTier.OWNER) == "OWNER"
    assert serialize.to_json_value({"tiers": (RoleTier.ADMIN,)}) == {"tiers": ["ADMIN"]}


def test_device_to_dict_uses_iso_datetime_and_plain_types() -> None:
    payload = serialize.device_to_dict(_device(), role="light")
    assert payload["observed_at"] == NOW.isoformat()
    assert payload == {
        "id": "office_light",
        "role": "light",
        "is_on": True,
        "brightness_pct": 70,
        "observed_at": NOW.isoformat(),
        "status": "observed",
        "changed_by": "system",
        "confidence": 1.0,
        "source": "demo.house",
        "raw_state": None,
        "cover_state": None,
        "lock_state": None,
        "climate_mode": None,
        "current_temperature": None,
        "target_temperature": None,
        "camera_available": None,
        "motion_detected": None,
    }


def test_room_person_context_and_status_dicts_are_lists() -> None:
    room = serialize.room_to_dict(
        room_id="office",
        name="Office",
        devices=[serialize.device_to_dict(_device(), role="light")],
        people=["Gerron"],
        camera=None,
    )
    assert room["devices"][0]["id"] == "office_light"
    assert isinstance(room["devices"], list)
    assert isinstance(room["people"], list)
    assert room["camera"] is None

    camera = serialize.camera_to_dict(camera_id="driveway_cam", label="Driveway", motion=False, online=True)
    assert camera == {"id": "driveway_cam", "label": "Driveway", "motion": False, "online": True}
    offline = serialize.camera_to_dict(camera_id="driveway_cam", label="Driveway", motion=False, online=False)
    assert offline["online"] is False

    person = serialize.person_to_dict(person_id="gerron", name="Gerron", room="office")
    assert person == {"id": "gerron", "name": "Gerron", "room": "office"}

    context = serialize.context_to_dict(context_id="working_late", label="Working late", active=True)
    assert context == {"id": "working_late", "label": "Working late", "active": True}

    status = serialize.status_to_dict(devices=5, people=1, line="Everything nominal")
    assert status == {"core": "Local Core", "devices": 5, "people": 1, "line": "Everything nominal"}


def test_pending_to_dict_emits_iso_expiry() -> None:
    pending = PendingRequest(
        request_id="request-1",
        rule_id="rule-1",
        title="Close the garage door?",
        detail="It has been open 18 minutes. No motion detected nearby.",
        expires_at=NOW + timedelta(minutes=5),
    )
    payload = serialize.pending_to_dict(pending)
    assert payload["expires_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert payload["request_id"] == "request-1"
    assert payload["title"] == "Close the garage door?"


def test_message_and_state_dicts_match_the_wire_shape() -> None:
    message = serialize.message_to_dict(sender="haven", text="Want me to close it?")
    assert message == {"from": "haven", "text": "Want me to close it?"}

    state = serialize.state_to_dict(
        glow="permission",
        glow_target="garage",
        revision=3,
        rooms=[
            serialize.room_to_dict(
                room_id="office",
                name="Office",
                devices=[serialize.device_to_dict(_device(), role="light")],
                people=["Gerron"],
                camera=None,
            )
        ],
        contexts=[serialize.context_to_dict(context_id="working_late", label="Working late", active=True)],
        people=[serialize.person_to_dict(person_id="gerron", name="Gerron", room="office")],
        pending=[],
        conversation=[message],
        activity=[{"event_id": "event-1", "event_type": "rule_approved", "actor_id": "gerron", "occurred_at": NOW.isoformat(), "summary": "Rule approved: close the garage by gerron"}],
        memory=[{"entry_id": "memory-1", "kind": "approved_rule", "content": "close the garage", "recorded_at": NOW.isoformat()}],
        status=serialize.status_to_dict(devices=5, people=1, line="HAVEN needs your attention"),
    )
    assert state["glow"] == "permission"
    assert state["glow_target"] == "garage"
    assert state["revision"] == 3
    for key in ("rooms", "contexts", "people", "pending", "conversation", "activity", "memory"):
        assert isinstance(state[key], list)
    assert state["activity"][0]["summary"] == "Rule approved: close the garage by gerron"
    assert state["memory"][0]["kind"] == "approved_rule"
    assert state["status"]["line"] == "HAVEN needs your attention"


def _event(event_type: EventType, payload: tuple[tuple[str, object], ...]) -> DomainEvent:
    return DomainEvent(
        event_id="event-1",
        household_id="household-demo",
        event_type=event_type,
        actor_id="gerron",
        occurred_at=NOW,
        payload=payload,
        correlation_id="rule-1",
        source="haven.core.store",
    )


def test_event_to_dict_renders_a_one_line_summary() -> None:
    approved = serialize.event_to_dict(
        _event(EventType.RULE_APPROVED, (("approved_by", "gerron"), ("justification", "ok"), ("rule_id", "rule-1"))),
        rule_label="close the garage",
    )
    assert approved == {
        "event_id": "event-1",
        "event_type": "rule_approved",
        "actor_id": "gerron",
        "occurred_at": NOW.isoformat(),
        "summary": "Rule approved: close the garage by gerron",
    }

    executed = serialize.event_to_dict(
        _event(
            EventType.ACTION_EXECUTED,
            (("action_id", "action-1"), ("request_id", "request-1"), ("result_source", "demo.house"), ("success", True)),
        ),
        action_device="driveway_cam",
    )
    assert executed["summary"] == "Action executed on driveway_cam (success=True)"

    blocked = serialize.event_to_dict(
        _event(
            EventType.ACTION_BLOCKED,
            (("action_id", "action-2"), ("code", "evidence_unavailable"), ("request_id", "request-2"), ("status", "unavailable")),
        ),
        action_device="driveway_cam",
    )
    assert blocked["summary"] == "Action blocked on driveway_cam (unavailable)"


def test_memory_to_dict_uses_iso_recorded_at() -> None:
    entry = MemoryEntry(
        entry_id="memory-1",
        household_id="household-demo",
        kind="approved_rule",
        content="close the garage when asked",
        source_event_id="event-1",
        recorded_at=NOW,
    )
    assert serialize.memory_to_dict(entry) == {
        "entry_id": "memory-1",
        "kind": "approved_rule",
        "content": "close the garage when asked",
        "recorded_at": NOW.isoformat(),
    }


def _draft(**overrides: object) -> RuleDraft:
    values: dict[str, object] = {
        "draft_id": "draft-1",
        "household_id": "household-demo",
        "proposed_by": "gerron",
        "source_text": "close the garage",
        "interpretation": "When the garage door has been open a while with no nearby motion, ask to close it.",
        "action_kind": ActionKind.CLOSE_GARAGE,
        "schedule_trigger": ScheduleTrigger(time_of_day=NOW.time(), window=timedelta(hours=24)),
        "target_device_id": "garage_door",
        "capability": "close",
    }
    values.update(overrides)
    return RuleDraft(**values)  # type: ignore[arg-type]


def _rule(**overrides: object) -> Rule:
    values: dict[str, object] = {
        "rule_id": "rule-1",
        "draft": _draft(),
        "status": RuleStatus.APPROVED,
        "approved_by": "gerron",
        "approved_by_role": RoleTier.OWNER,
        "approved_at": NOW,
    }
    values.update(overrides)
    return Rule(**values)  # type: ignore[arg-type]


def test_rule_to_dict_resolves_room_and_names_the_capability() -> None:
    rule = _rule()

    with_room = serialize.rule_to_dict(rule, device_room="garage")
    assert with_room == {
        "rule_id": "rule-1",
        "summary": "close the garage",
        "action": "close",
        "target": "garage_door · Garage",
        "status": "approved",
        "approved_at": NOW.isoformat(),
    }

    without_room = serialize.rule_to_dict(rule)
    assert without_room["target"] == "garage_door"
    assert without_room["status"] == "approved"


def test_rule_to_dict_falls_back_without_capability_and_approval() -> None:
    rule = _rule(
        draft=_draft(capability=None),
        status=RuleStatus.PROPOSED,
        approved_by=None,
        approved_by_role=None,
        approved_at=None,
    )
    payload = serialize.rule_to_dict(rule)
    assert payload["action"] == "close_garage"
    assert payload["approved_at"] is None
    assert payload["status"] == "proposed"


def test_rule_to_dict_collapses_and_truncates_long_source_text() -> None:
    rule = _rule(
        draft=_draft(
            source_text=(
                "close the garage\n\twhen it has been open for a very long time and there is no motion "
                "anywhere nearby at all, even the tiniest bit"
            )
        )
    )
    summary = serialize.rule_to_dict(rule)["summary"]
    assert len(summary) == 90
    assert summary.endswith("…")
    assert "\n" not in summary and "\t" not in summary

    # An empty source text falls back to the interpretation.
    draft = _draft(source_text="placeholder")
    object.__setattr__(draft, "source_text", " ")
    fallback = serialize.rule_to_dict(_rule(draft=draft))
    assert fallback["summary"] == (
        "When the garage door has been open a while with no nearby motion, ask to close it."
    )


def test_rule_to_dict_describes_selector_targets() -> None:
    rule = _rule(
        draft=_draft(
            capability="power",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            target_device_id=None,
            target_selector=DeviceSelector(role="light", room="office"),
        )
    )
    assert serialize.rule_to_dict(rule)["target"] == "office · light"


def test_engine_provider_and_system_dicts_match_the_wire_shape() -> None:
    engine = serialize.engine_to_dict(human_override_window=timedelta(minutes=90), minimum_confidence=1.0)
    assert engine == {"human_override_minutes": 90, "minimum_confidence": 1.0}

    provider = serialize.provider_to_dict(
        ProviderCapabilities(
            provider_id="haven.fixture_intelligence",
            kind="intelligence",
            capabilities=("chat", "explain", "interpret", "propose_rule"),
        )
    )
    assert provider == {
        "kind": "intelligence",
        "provider_id": "haven.fixture_intelligence",
        "capabilities": ["chat", "explain", "interpret", "propose_rule"],
    }

    system = serialize.system_to_dict(
        revision=3,
        event_count=12,
        memory_count=4,
        engine=engine,
        providers=[provider],
    )
    assert system == {
        "revision": 3,
        "event_count": 12,
        "memory_count": 4,
        "engine": {"human_override_minutes": 90, "minimum_confidence": 1.0},
        "providers": [provider],
    }


def test_state_dict_carries_automations_and_system_with_defaults() -> None:
    rule_payload = serialize.rule_to_dict(_rule(), device_room="garage")
    state = serialize.state_to_dict(
        glow="idle",
        revision=1,
        rooms=[],
        contexts=[],
        people=[],
        pending=[],
        conversation=[],
        automations=[rule_payload],
        status=serialize.status_to_dict(devices=5, people=1, line="Everything nominal"),
    )
    assert state["automations"] == [rule_payload]
    assert state["system"]["revision"] == 0
    assert state["system"]["providers"] == []

    defaulted = serialize.state_to_dict(
        glow="idle",
        revision=1,
        rooms=[],
        contexts=[],
        people=[],
        pending=[],
        conversation=[],
        status=serialize.status_to_dict(devices=5, people=1, line="Everything nominal"),
    )
    assert defaulted["automations"] == []
    assert defaulted["system"]["event_count"] == 0
    assert defaulted["system"]["engine"] == {"human_override_minutes": 90, "minimum_confidence": 1.0}
    assert defaulted["system"]["providers"] == []
