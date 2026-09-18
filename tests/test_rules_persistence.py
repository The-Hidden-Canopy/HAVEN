"""Rules persistence: the codec, the file store, the store rehydration
boundary, and the director/boot wiring that makes automations survive a
restart.

Doctrine under test: the core store is transition-only, so rehydration is a
seam with its own contract -- it replays NO transitions and emits NO events,
it replaces the rule set wholesale, and one malformed sidecar row must never
eat the household's other automations.
"""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from haven.core.domain import (
    ActionKind,
    DeviceSelector,
    Principal,
    PredictionTrigger,
    RoleTier,
    Rule,
    RuleDraft,
    RuleStatus,
    ScheduleTrigger,
)
from haven.core.store import HavenStore
from haven.errors import InvalidTransition, ScopeViolation
from haven.execution import ExecutionProviderRegistry
from haven.intelligence.gateway import ScriptedIntelligenceProvider, UnsupportedIntent
from haven.runtime import HavenRuntime
from haven.web.application import HA_PROVIDER_ID
from haven.web.demo import HOUSEHOLD_ID, DemoDirector
from haven.web.rules_persist import RulesPersistence, rule_from_dict, rule_to_dict
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore
from haven.web.setup_service import _ENROLLED_FILENAME, _HOUSEHOLD_FILENAME, _TOKEN_FILENAME

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 22, 35, tzinfo=UTC)

RESIDENT = Principal(actor_id="resident-1", household_id="household-a", role_tier=RoleTier.MEMBER)


def _office_light_rule(*, household_id: str = "household-a", rule_id: str = "rule-office") -> Rule:
    return Rule(
        rule_id=rule_id,
        draft=RuleDraft(
            draft_id="draft-office-light-off",
            household_id=household_id,
            proposed_by="resident-1",
            source_text="turn off the office light at 22:35 every day",
            interpretation="Turn off the office light at 22:35 every night.",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            schedule_trigger=ScheduleTrigger(
                time_of_day=time(22, 35),
                weekdays=frozenset({0, 2, 4}),
                window=timedelta(minutes=10),
            ),
            target_device_id="office_light",
            parameters=(("ramp_seconds", 30),),
            capability="power",
        ),
        status=RuleStatus.APPROVED,
        approved_by="owner-1",
        approved_by_role=RoleTier.OWNER,
        approved_at=NOW,
    )


def _selector_draft_rule() -> Rule:
    return Rule(
        rule_id="rule-selector",
        draft=RuleDraft(
            draft_id="draft-selector",
            household_id="household-a",
            proposed_by="resident-1",
            source_text="turn off every bedroom light when a storm is predicted",
            interpretation="Turn off every bedroom light when a storm is predicted for the house.",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            prediction_trigger=PredictionTrigger(event="storm", min_confidence=0.8, subject_id="house"),
            target_selector=DeviceSelector(role="light", room="bedroom"),
            assumptions=("the bedroom lights are the lights in the bedroom",),
            expires_at=NOW + timedelta(days=30),
        ),
        status=RuleStatus.PROPOSED,
    )


# -- the codec ----------------------------------------------------------------


def test_rule_codec_round_trips_an_approved_schedule_rule() -> None:
    rule = _office_light_rule()
    assert rule_from_dict(rule_to_dict(rule)) == rule


def test_rule_codec_round_trips_a_draft_with_a_target_selector() -> None:
    rule = _selector_draft_rule()
    restored = rule_from_dict(rule_to_dict(rule))
    assert restored == rule
    assert restored.draft.target_selector == DeviceSelector(role="light", room="bedroom")
    assert restored.draft.prediction_trigger == PredictionTrigger(
        event="storm", min_confidence=0.8, subject_id="house"
    )


def test_rule_from_dict_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError, match="rule payload must be a mapping"):
        rule_from_dict(["not", "a", "mapping"])
    rule = _office_light_rule()
    payload = rule_to_dict(rule)
    with pytest.raises(ValueError, match="missing key"):
        rule_from_dict({key: value for key, value in payload.items() if key != "status"})
    broken = rule_to_dict(rule)
    broken["draft"]["schedule_trigger"]["weekdays"] = [9]
    with pytest.raises(ValueError, match="weekdays"):
        rule_from_dict(broken)
    broken = rule_to_dict(rule)
    broken["draft"]["schedule_trigger"]["window"] = -5
    with pytest.raises(ValueError, match="window"):
        rule_from_dict(broken)
    broken = rule_to_dict(rule)
    broken["draft"]["schedule_trigger"]["time_of_day"] = "not-a-time"
    with pytest.raises(ValueError, match="time_of_day"):
        rule_from_dict(broken)
    broken = rule_to_dict(rule)
    broken["approved_at"] = "2026-09-16T22:35:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        rule_from_dict(broken)


# -- the file store -----------------------------------------------------------


def test_persistence_load_missing_file_is_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        persistence = RulesPersistence(Path(tmp) / "rules.json")
        assert persistence.load().rules == ()


def test_persistence_skips_one_bad_row_and_keeps_the_good_ones() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rules.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [{"garbage": True}, rule_to_dict(_office_light_rule()), "not a mapping"],
                    "scheduler_enabled": {"rule-office": False, "rule-x": "yes", "rule-y": 1},
                }
            ),
            encoding="utf-8",
        )
        snapshot = RulesPersistence(path).load()
        assert snapshot.rules == (_office_light_rule(),)
        assert snapshot.scheduler_enabled == {"rule-office": False}


def test_persistence_load_garbage_bytes_is_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rules.json"
        path.write_bytes(b"\x00 not json \xff")
        assert RulesPersistence(path).load().rules == ()


def test_persistence_save_then_load_round_trips() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        persistence = RulesPersistence(Path(tmp) / "rules.json")
        rule = _office_light_rule()
        persistence.save((rule,), {"rule-office": False})
        snapshot = persistence.load()
        assert snapshot.rules == (rule,)
        assert snapshot.scheduler_enabled == {"rule-office": False}
        on_disk = json.loads(persistence.path.read_text(encoding="utf-8"))
        assert on_disk["version"] == 1
        assert on_disk["scheduler_enabled"] == {"rule-office": False}


# -- the rehydration boundary -------------------------------------------------


def _store_with_proposed_rule() -> tuple[HavenStore, Rule]:
    store = HavenStore(household_id="household-a")
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        execution_providers=ExecutionProviderRegistry(),
    )
    rule = runtime.propose_draft(_selector_draft_rule().draft, principal=RESIDENT, now=NOW)
    return store, rule


def test_restore_rules_replaces_the_rule_set_wholesale() -> None:
    store, proposed = _store_with_proposed_rule()
    assert store.state.rules == (proposed,)
    replacement = _office_light_rule()
    store.restore_rules((replacement,))
    assert store.state.rules == (replacement,)


def test_restore_rules_rejects_a_cross_household_rule() -> None:
    store, _ = _store_with_proposed_rule()
    foreign = _office_light_rule(household_id="household-b", rule_id="rule-foreign")
    with pytest.raises(ScopeViolation):
        store.restore_rules((foreign,))


def test_restore_rules_rejects_duplicate_rule_ids() -> None:
    store, _ = _store_with_proposed_rule()
    first = _office_light_rule()
    duplicate = Rule(
        rule_id=first.rule_id,
        draft=RuleDraft(
            draft_id="draft-other",
            household_id="household-a",
            proposed_by="resident-1",
            source_text="close the garage at midnight",
            interpretation="Close the garage at midnight.",
            action_kind=ActionKind.CLOSE_GARAGE,
            schedule_trigger=ScheduleTrigger(time_of_day=time(0, 0), window=timedelta(minutes=5)),
            target_device_id="garage_door",
        ),
    )
    assert duplicate.rule_id == first.rule_id
    with pytest.raises(InvalidTransition):
        store.restore_rules((first, duplicate))


def test_restore_rules_emits_no_events() -> None:
    store, _ = _store_with_proposed_rule()
    event_count = len(store.events)
    store.restore_rules((_office_light_rule(),))
    assert len(store.events) == event_count


# -- the director wiring ------------------------------------------------------


class _RuleDraftingProvider:
    """A deterministic stand-in that drafts one clean schedule rule from chat."""

    def interpret(self, text, *, principal, now):
        raise UnsupportedIntent

    def chat(self, context, message):
        raise UnsupportedIntent

    def propose_rule(self, context, message):
        raise UnsupportedIntent

    def explain(self, context, decision):
        raise UnsupportedIntent

    def interpret_intent(self, text, *, context, principal, now):
        return RuleDraft(
            draft_id="draft-chat-schedule",
            household_id=principal.household_id,
            proposed_by=principal.actor_id,
            source_text=text,
            interpretation="Turn off the office light at 22:35 every night.",
            action_kind=ActionKind.TURN_LIGHT_OFF,
            schedule_trigger=ScheduleTrigger(time_of_day=time(22, 35), window=timedelta(minutes=10)),
            target_device_id="office_light",
            capability="power",
        )


def _director(persistence: RulesPersistence | None) -> DemoDirector:
    return DemoDirector(
        clock=lambda: NOW,
        scenario=False,
        intelligence_provider=_RuleDraftingProvider(),
        rules_persistence=persistence,
    )


def test_director_chat_rule_survives_a_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        persistence = RulesPersistence(Path(tmp) / "rules.json")
        first = _director(persistence)
        first.chat("when it's late, turn off the office light")

        assert persistence.path.exists()
        assert len(first.store.state.rules) == 1
        rule_id = first.store.state.rules[0].rule_id
        assert first.store.state.rules[0].status == RuleStatus.APPROVED

        # The scheduler enabled map set through the director wrapper persists too.
        first.set_scheduler_enabled(rule_id, False)

        second = _director(persistence)
        restored = second.store.state.rules
        assert len(restored) == 1
        assert restored[0].rule_id == rule_id
        assert restored[0].status == RuleStatus.APPROVED
        assert restored[0].draft.source_text == "when it's late, turn off the office light"
        assert second.scheduler.is_enabled(rule_id) is False


def test_director_with_scenario_and_empty_sidecar_keeps_seeded_rules() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        persistence = RulesPersistence(Path(tmp) / "rules.json")
        director = DemoDirector(clock=lambda: NOW, rules_persistence=persistence)
        assert director.garage_rule_id is not None
        assert {rule.rule_id for rule in director.store.state.rules} == {
            director.garage_rule_id,
            director.camera_rule_id,
            director.office_light_rule_id,
        }
        # The seeded enabled set round-tripped through the file: garage and
        # camera stay disabled, the office-light schedule stays enabled.
        assert director.scheduler.is_enabled(director.garage_rule_id) is False
        assert director.scheduler.is_enabled(director.camera_rule_id) is False
        assert director.scheduler.is_enabled(director.office_light_rule_id) is True


def test_director_boot_drops_rules_from_a_different_household_id() -> None:
    """A sidecar written under a different household_id must never crash boot.

    This is the migration hazard `ensure_household_id` introduces: a
    `rules.json` written by an older process lifetime (or, before every
    installation had its own permanent id, literally every installation)
    can name a household_id that no longer matches this run's. `HavenStore`
    already refuses that at the transition boundary (`ScopeViolation`); the
    director must degrade by dropping the untrusted rules, not propagate the
    exception into a boot crash.
    """

    with tempfile.TemporaryDirectory() as tmp:
        persistence = RulesPersistence(Path(tmp) / "rules.json")
        persistence.save((_office_light_rule(household_id="household-other"),))

        director = DemoDirector(
            clock=lambda: NOW,
            scenario=False,
            household_id="household-mine",
            resident=Principal(actor_id="resident-1", household_id="household-mine", role_tier=RoleTier.MEMBER),
            owner=Principal(actor_id="owner-1", household_id="household-mine", role_tier=RoleTier.OWNER),
            rules_persistence=persistence,
        )

    assert director.store.state.rules == ()


def test_director_without_persistence_writes_nothing_and_stays_ephemeral() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        director = _director(None)
        director.chat("when it's late, turn off the office light")
        assert director._rules_persistence is None
        assert len(director.store.state.rules) == 1
        assert list(Path(tmp).rglob("rules.json")) == []


# -- the boot-level acceptance ------------------------------------------------


class _StubStatesSource:
    """The structural `fetch_states()` contract, canned for tests."""

    def __init__(self, states) -> None:
        self._states = states

    def fetch_states(self) -> tuple[dict, ...]:
        return self._states


_CHANGED = "2026-09-16T19:59:30+00:00"
LIVE_STATES = (
    {"entity_id": "light.office_desk", "state": "on", "attributes": {}, "last_changed": _CHANGED},
)

# The scripted floor drafts exactly this phrase; its draft is intentionally
# unresolved, so the rule is proposed (not approved) -- persistence must
# carry it across the restart regardless of status.
_RULE_PHRASE = "when i'm working late, don't blast the bedroom lights when i walk in."


def _seed_real_setup(data_dir: Path) -> None:
    """Persist a configured-provider installation, like the factory reads."""
    data_dir.mkdir(parents=True, exist_ok=True)
    store = SetupConfigStore(data_dir / "haven.json")
    store.save(
        SetupConfig(
            completed=True,
            data_dir=str(data_dir),
            provider_kind="home_assistant",
            provider_base_url="http://ha.local:8123",
            provider_token_file=_TOKEN_FILENAME,
        )
    )
    (data_dir / _TOKEN_FILENAME).write_text("secret-token", encoding="utf-8")
    (data_dir / _ENROLLED_FILENAME).write_text(json.dumps({"version": 2, "manifests": []}), encoding="utf-8")
    # A declared owner: governed actions (chat rule drafting/approval) now
    # refuse without one, and this test is about persistence surviving a
    # restart, not about that separate refusal.
    (data_dir / _HOUSEHOLD_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "people": [
                    {
                        "person_id": "resident-1",
                        "name": "Resident One",
                        "role": "owner",
                        "sources": [],
                    }
                ],
                "contexts": [],
            }
        ),
        encoding="utf-8",
    )


@contextmanager
def _boot(data_dir: Path, ha_client):
    instance, director = make_server(0, data_dir=str(data_dir), clock=lambda: NOW, ha_client=ha_client)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, director, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else ""
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def test_chat_rule_survives_a_real_mode_reboot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, first_director, port):
            assert first_director.house is None  # real mode, not the demo fallback
            status, _ = _post(port, "/api/chat", {"text": _RULE_PHRASE})
            assert status == 200
            automations = first_director.state()["automations"]
            assert len(automations) == 1
            rule_id = automations[0]["rule_id"]

        # The sidecar landed next to the setup config.
        assert (data_dir / "rules.json").exists()

        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, second_director, port):
            status, body = _get_json(port, "/api/state")
            assert status == 200
            restored_ids = {automation["rule_id"] for automation in body["automations"]}
            assert rule_id in restored_ids
            rule = second_director.store.get_rule(rule_id)
            assert rule.draft.source_text == _RULE_PHRASE
            assert rule.status == RuleStatus.PROPOSED
