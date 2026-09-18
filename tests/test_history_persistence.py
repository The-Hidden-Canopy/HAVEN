"""Durable history: events, actions, receipts, and memory survive a restart.

Before `HistoryStore` existed, `HavenState.actions`/`.memory` and
`HavenStore.events` were pure process memory -- `rules.json` already let
automations survive a restart, but the Activity feed, the receipts ledger,
and every action drill-down reset to empty on every boot regardless. This
file proves the acceptance criterion directly: execute an action, restart
the whole server against the same data dir, and the action, its authority
decision, its consequence, and its receipt are still there.
"""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from haven.core.domain import (
    ActionKind,
    ActionOrigin,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    AuthorityDecision,
    ConfirmationToken,
    DecisionCode,
    DecisionStatus,
    DeviceResult,
    DomainEvent,
    EventType,
    EvidenceRef,
    EvidenceStatus,
    MemoryEntry,
    RoleTier,
)
from haven.audit.receipts import ActionReceipt
from haven.errors import ScopeViolation
from haven.web.history_persist import (
    HistoryStore,
    action_record_from_dict,
    action_record_to_dict,
    domain_event_from_dict,
    domain_event_to_dict,
    memory_entry_from_dict,
    memory_entry_to_dict,
    receipt_from_dict,
    receipt_to_dict,
)
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore
from haven.web.setup_service import _ENROLLED_FILENAME, _HOUSEHOLD_FILENAME, _TOKEN_FILENAME
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.web.application import HA_PROVIDER_ID

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
HOUSEHOLD = "household-a"


def _request() -> ActionRequest:
    return ActionRequest(
        request_id="request-1",
        household_id=HOUSEHOLD,
        requested_by="ada",
        rule_id="request-1",
        action_kind=ActionKind.TURN_LIGHT_OFF,
        target_device_id="light.office",
        parameters=(("brightness_pct", 40),),
        justification="turning off the office light",
        evidence_snapshot_id="snapshot-1",
        requested_at=NOW,
        origin=ActionOrigin.DIRECT,
        confirmation_token=ConfirmationToken(
            token_id="token-1",
            household_id=HOUSEHOLD,
            rule_id="request-1",
            request_id="request-1",
            confirmed_by="ada",
            issued_at=NOW,
            expires_at=NOW.replace(minute=5),
        ),
        capability="power",
    )


def _decision() -> AuthorityDecision:
    return AuthorityDecision(
        status=DecisionStatus.ALLOW, code=DecisionCode.ALLOWED, explanation="allowed", required_role=RoleTier.MEMBER
    )


def _result() -> DeviceResult:
    return DeviceResult(success=True, detail="http_200", observed_at=NOW, source="home_assistant.rest")


# -- codec round trips --------------------------------------------------------


def test_action_request_round_trip_drops_the_confirmation_token():
    request = _request()
    restored = action_record_from_dict(
        action_record_to_dict(ActionRecord(action_id="action-1", request=request, status=ActionStatus.EXECUTED, decision=_decision(), executed_at=NOW, result=_result()))
    )
    assert restored.action_id == "action-1"
    assert restored.request.request_id == request.request_id
    assert restored.request.capability == "power"
    assert restored.request.parameters == request.parameters
    # Confirmation material is never persisted -- see history_persist's module docstring.
    assert restored.request.confirmation_token is None
    assert restored.status == ActionStatus.EXECUTED
    assert restored.decision.status == DecisionStatus.ALLOW
    assert restored.result.success is True
    assert restored.executed_at == NOW


def test_domain_event_round_trips():
    event = DomainEvent(
        event_id="event-1",
        household_id=HOUSEHOLD,
        event_type=EventType.ACTION_EXECUTED,
        actor_id="ada",
        occurred_at=NOW,
        payload=(("action_id", "action-1"), ("success", True)),
        correlation_id="request-1",
        source="haven.core.store",
    )
    restored = domain_event_from_dict(domain_event_to_dict(event))
    assert restored == event


def test_memory_entry_round_trips():
    entry = MemoryEntry(
        entry_id="memory-1", household_id=HOUSEHOLD, kind="approved_rule",
        content="turn off the office light", source_event_id="event-1", recorded_at=NOW,
    )
    assert memory_entry_from_dict(memory_entry_to_dict(entry)) == entry


def test_receipt_round_trip_preserves_evidence_and_drops_the_token():
    receipt = ActionReceipt(
        receipt_id="receipt-1",
        requested_action=_request(),
        interpretation="turning off the office light",
        evidence=(EvidenceRef(kind="presence", subject_id="ada:office", status=EvidenceStatus.OBSERVED, observed_at=NOW, source="demo.house"),),
        decision=_decision(),
        device_result=_result(),
        event_ids=("event-1", "event-2"),
    )
    restored = receipt_from_dict(receipt_to_dict(receipt))
    assert restored.receipt_id == "receipt-1"
    assert restored.requested_action.confirmation_token is None
    assert restored.evidence[0].subject_id == "ada:office"
    assert restored.decision.status == DecisionStatus.ALLOW
    assert restored.device_result.success is True
    assert restored.event_ids == ("event-1", "event-2")


# -- HistoryStore --------------------------------------------------------------


def test_history_store_append_and_load_round_trips_everything():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(Path(tmp) / "history.db")
        event = DomainEvent(
            event_id="event-1", household_id=HOUSEHOLD, event_type=EventType.ACTION_AUTHORIZED,
            actor_id="ada", occurred_at=NOW, payload=(("action_id", "action-1"),),
            correlation_id="request-1", source="haven.core.store",
        )
        action = ActionRecord(action_id="action-1", request=_request(), status=ActionStatus.AUTHORIZED, decision=_decision())
        memory = MemoryEntry(entry_id="memory-1", household_id=HOUSEHOLD, kind="approved_rule", content="x", source_event_id="event-1", recorded_at=NOW)
        receipt = ActionReceipt(receipt_id="receipt-1", requested_action=_request(), interpretation="x", evidence=(), decision=_decision(), device_result=None, event_ids=("event-1",))

        store.append_event(event)
        store.save_action(action)
        store.append_memory(memory)
        store.append_receipt(receipt)

        snapshot = store.load(HOUSEHOLD)
        assert [e.event_id for e in snapshot.events] == ["event-1"]
        assert [a.action_id for a in snapshot.actions] == ["action-1"]
        assert [m.entry_id for m in snapshot.memory] == ["memory-1"]
        assert [r.receipt_id for r in snapshot.receipts] == ["receipt-1"]


def test_history_store_save_action_upserts_by_action_id():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(Path(tmp) / "history.db")
        authorized = ActionRecord(action_id="action-1", request=_request(), status=ActionStatus.AUTHORIZED, decision=_decision())
        store.save_action(authorized)
        executed = ActionRecord(
            action_id="action-1", request=_request(), status=ActionStatus.EXECUTED, decision=_decision(),
            executed_at=NOW, result=_result(),
        )
        store.save_action(executed)

        snapshot = store.load(HOUSEHOLD)
        assert len(snapshot.actions) == 1
        assert snapshot.actions[0].status == ActionStatus.EXECUTED
        assert snapshot.actions[0].result.success is True


def test_history_store_writes_are_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(Path(tmp) / "history.db")
        event = DomainEvent(
            event_id="event-1", household_id=HOUSEHOLD, event_type=EventType.ACTION_AUTHORIZED,
            actor_id="ada", occurred_at=NOW, payload=(), correlation_id="request-1", source="haven.core.store",
        )
        store.append_event(event)
        store.append_event(event)  # duplicate insert must not raise or duplicate the row
        assert len(store.load(HOUSEHOLD).events) == 1


def test_history_store_skips_one_bad_row_and_keeps_the_rest():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "history.db"
        store = HistoryStore(path)
        good = DomainEvent(
            event_id="event-good", household_id=HOUSEHOLD, event_type=EventType.ACTION_AUTHORIZED,
            actor_id="ada", occurred_at=NOW, payload=(), correlation_id="request-1", source="haven.core.store",
        )
        store.append_event(good)
        store._write(
            "INSERT INTO events(event_id, household_id, occurred_at, data) VALUES (?, ?, ?, ?)",
            ("event-bad", HOUSEHOLD, NOW.isoformat(), json.dumps({"not": "a valid event"})),
        )
        snapshot = store.load(HOUSEHOLD)
        assert [e.event_id for e in snapshot.events] == ["event-good"]


def test_history_store_leaves_no_open_handle_after_use():
    # Regression guard: HistoryStore must never hold a connection open past
    # a single call, or a Windows host cannot delete its data directory
    # while the process (or even this test) still references the store.
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(Path(tmp) / "history.db")
        store.append_event(
            DomainEvent(
                event_id="event-1", household_id=HOUSEHOLD, event_type=EventType.ACTION_AUTHORIZED,
                actor_id="ada", occurred_at=NOW, payload=(), correlation_id="request-1", source="haven.core.store",
            )
        )
        store.load(HOUSEHOLD)
    # No assertion needed beyond reaching here: on Windows, TemporaryDirectory
    # cleanup above raises PermissionError if any handle is still open.


# -- end-to-end acceptance: survives a real server restart --------------------


class _FakeHAResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


LIVE_STATES = (
    {"entity_id": "light.office", "state": "on", "attributes": {}, "last_changed": "2026-09-16T19:59:30+00:00"},
)


def _fake_urlopen(request, timeout=None):
    if request.get_method() == "GET":
        return _FakeHAResponse(200, json.dumps(LIVE_STATES).encode("utf-8"))
    return _FakeHAResponse(200, b"[]")


def _seed_real_setup(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    SetupConfigStore(data_dir / "haven.json").save(
        SetupConfig(
            completed=True, data_dir=str(data_dir), provider_kind="home_assistant",
            provider_base_url="http://ha.local:8123", provider_token_file=_TOKEN_FILENAME,
        )
    )
    (data_dir / _TOKEN_FILENAME).write_text("secret-token", encoding="utf-8")
    manifest = DeviceManifest(
        device_id="light.office", device_type="light", provider_id=HA_PROVIDER_ID, room="office",
        capabilities=(CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),),
    )
    (data_dir / _ENROLLED_FILENAME).write_text(json.dumps({"version": 2, "manifests": [manifest.to_dict()]}), encoding="utf-8")
    (data_dir / _HOUSEHOLD_FILENAME).write_text(
        json.dumps({"version": 1, "people": [{"person_id": "ada", "name": "Ada", "role": "owner", "sources": []}], "contexts": []}),
        encoding="utf-8",
    )


@contextmanager
def _boot(data_dir: Path):
    instance, director = make_server(0, data_dir=str(data_dir), clock=lambda: NOW)
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


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def test_action_and_receipt_survive_a_real_mode_reboot():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)

        with patch("haven.integrations.home_assistant.client.urlopen", side_effect=_fake_urlopen):
            with _boot(data_dir) as (_, first_director, port):
                assert first_director.house is None  # real mode, not the demo fallback
                status, body = _post(port, "/api/devices/light.office/command", {"service": "light.turn_off"})
                assert status == 200

                _, state = _get_json(port, "/api/state")
                event_id = next(
                    row["event_id"] for row in state["activity"] if row["event_type"] == "action_executed"
                )
                _, before = _get_json(port, f"/api/actions/chain?event_id={event_id}")
                assert before["chain"]["outcome"] == "executed"
                action_id = before["chain"]["action_id"]

            assert (data_dir / "history.db").exists()

            with _boot(data_dir) as (_, second_director, port):
                assert second_director.store.get_action(action_id) is not None
                status, after = _get_json(port, f"/api/actions/chain?event_id={event_id}")
                assert status == 200
                assert after["chain"] is not None
                assert after["chain"]["action_id"] == action_id
                assert after["chain"]["outcome"] == "executed"
                assert after["chain"]["request"]["target_device_id"] == "light.office"
                assert after["chain"]["authority"]["status"] == "allow"
                assert after["chain"]["execution"]["attempted"] is True
                assert after["chain"]["consequence"]["observed"] is True

                _, state = _get_json(port, "/api/state")
                assert any(row["event_type"] == "action_executed" for row in state["activity"])


def test_havenstore_restore_methods_reject_cross_household_rows():
    """`HavenStore.restore_events/_actions/_memory` are the safety net a
    `HistoryStore.load(household_id)` scoped query cannot itself replace --
    e.g. if `load` is ever called with the wrong id, or a future change to
    it stops filtering correctly, these must still refuse to admit
    another household's history rather than silently mixing it in.
    """

    from haven.core.store import HavenStore

    store = HavenStore(household_id="household-mine")
    foreign_event = DomainEvent(
        event_id="event-foreign", household_id="household-other", event_type=EventType.ACTION_AUTHORIZED,
        actor_id="ada", occurred_at=NOW, payload=(), correlation_id="request-1", source="haven.core.store",
    )
    with pytest.raises(ScopeViolation):
        store.restore_events((foreign_event,))

    foreign_action = ActionRecord(
        action_id="action-foreign",
        request=ActionRequest(
            request_id="r", household_id="household-other", requested_by="ada", rule_id="r",
            action_kind=ActionKind.TURN_LIGHT_OFF, target_device_id="light.office", parameters=(),
            justification="x", evidence_snapshot_id="s", requested_at=NOW, origin=ActionOrigin.DIRECT,
        ),
        status=ActionStatus.AUTHORIZED,
        decision=_decision(),
    )
    with pytest.raises(ScopeViolation):
        store.restore_actions((foreign_action,))

    foreign_memory = MemoryEntry(
        entry_id="memory-foreign", household_id="household-other", kind="approved_rule",
        content="x", source_event_id="event-1", recorded_at=NOW,
    )
    with pytest.raises(ScopeViolation):
        store.restore_memory((foreign_memory,))
