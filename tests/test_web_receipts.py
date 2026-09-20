"""Trust-chain drill-down: the receipts_api payload builder plus the two
chain routes (action id and activity-row event id) over a live server."""

import http.client
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from haven.web.demo import DemoDirector
from haven.web.receipts_api import action_chain, event_action_id
from haven.web.server import make_server

UTC = timezone.utc
FIXED_NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


@pytest.fixture()
def director():
    return DemoDirector(clock=lambda: FIXED_NOW)


def _chain_for(director, action, *, now=FIXED_NOW):
    return action_chain(
        action.action_id,
        store=director.store,
        now=now,
        receipts=director.receipts,
        device_registry=director.engine.device_registry,
        executed_commands=director.adapter.commands,
    )


def _approve_garage(director):
    (pending,) = director.pending_requests
    director.approve(pending.request_id)
    return next(a for a in director.store.state.actions if a.status.value == "executed")


def test_chain_sections_for_an_approved_garage_close(director) -> None:
    action = _approve_garage(director)
    chain = _chain_for(director, action)

    assert chain["action_id"] == action.action_id
    assert chain["status"] == "executed"

    request = chain["request"]
    assert request["action_kind"] == "close_garage"
    assert request["target_device_id"] == "garage_door"
    assert request["origin"] == "rule"
    assert request["rule_id"] == director.garage_rule_id
    assert request["actor"] == "gerron"
    assert request["confirmation_present"] is True
    assert request["requested_at"] == FIXED_NOW.isoformat()
    assert request["requested_seconds_ago"] == 0.0

    interpretation = chain["interpretation"]
    assert interpretation["basis"] == "rule_interpretation"
    assert "garage" in interpretation["text"]
    assert interpretation["source_text"] == "close the garage"

    evidence = chain["evidence"]
    assert evidence, "the rule run recorded world evidence"
    device_evidence = next(item for item in evidence if item["kind"] == "device")
    assert device_evidence["subject"] == "garage_door"
    assert device_evidence["confidence"] is None
    assert device_evidence["observed_seconds_ago"] == (FIXED_NOW - datetime(
        2026, 9, 16, 19, 42, tzinfo=UTC
    )).total_seconds()
    assert device_evidence["fresh"] is False  # garage state is 18 minutes old

    authority = chain["authority"]
    assert authority["status"] == "allow"
    assert authority["code"] == "allowed"
    assert authority["explanation"]
    # The receipt carries no risk; the chain derives it through the same
    # risk_for_request path the engine used and labels it as derived.
    assert authority["risk_tier"] == "confirmation_required"
    assert "does not record risk" in authority["risk_note"]

    execution = chain["execution"]
    assert execution["attempted"] is True
    assert execution["service"] == "cover.close"
    assert execution["service_basis"] == "executed_command"
    assert execution["provider_id"] == "simulated_house"
    assert execution["success"] is True
    assert execution["detail"] == "Garage door closed."

    consequence = chain["consequence"]
    assert consequence["observed"] is True
    assert "garage_door" in consequence["summary"]
    assert consequence["delay_ms"] == 0.0  # the demo executes synchronously
    assert consequence["observed_at"] == FIXED_NOW.isoformat()

    timeline = chain["timeline"]
    assert timeline
    assert len(timeline) <= 20
    assert [row["at"] for row in timeline] == sorted(row["at"] for row in timeline)
    own_types = {row["event_type"] for row in timeline[:2]}
    assert own_types == {"action_authorized", "action_executed"}
    # Rule actions correlate by rule id, so the rule lifecycle rides along.
    assert "rule_approved" in {row["event_type"] for row in timeline}


def test_chain_for_a_direct_light_off(director) -> None:
    director.chat("turn that light off", focus="office")
    action = next(a for a in director.store.state.actions if a.status.value == "executed")
    chain = _chain_for(director, action)

    assert chain["request"]["origin"] == "direct"
    assert chain["request"]["rule_id"] is None  # rule_id is only a correlation key
    assert chain["interpretation"]["basis"] == "direct_justification"
    assert chain["interpretation"]["text"] == action.request.justification
    # A direct command asserts its own evidence; the receipt records none.
    assert chain["evidence"] == []
    assert chain["authority"]["status"] == "allow"
    assert chain["authority"]["risk_tier"] == "safe_automatic"
    assert chain["execution"]["service"] == "light.turn_off"
    assert chain["execution"]["service_basis"] == "executed_command"
    assert chain["consequence"]["observed"] is True


def test_chain_for_a_blocked_action_is_honest_about_the_gap(director) -> None:
    # The scenario's initial ask is recorded blocked (confirmation required).
    blocked = next(a for a in director.store.state.actions if a.status.value == "blocked")
    chain = _chain_for(director, blocked)

    assert chain["status"] == "blocked"
    assert chain["authority"]["status"] == "confirmation_required"
    assert chain["authority"]["code"] == "confirmation_required"
    assert chain["authority"]["risk_tier"] == "confirmation_required"
    assert chain["execution"]["attempted"] is False
    assert chain["consequence"]["observed"] is False
    assert chain["consequence"]["note"] == "not yet observed"
    assert chain["timeline"][0]["event_type"] == "action_blocked"


def test_chain_omits_relative_fields_without_now(director) -> None:
    action = _approve_garage(director)
    chain = action_chain(action.action_id, store=director.store)
    assert chain["request"]["requested_seconds_ago"] is None
    assert chain["request"]["requested_at"] == FIXED_NOW.isoformat()
    assert chain["authority"]["risk_tier"] is None  # no registry, muted note instead
    assert "no device registry" in chain["authority"]["risk_note"]


def test_unknown_action_returns_none(director) -> None:
    assert action_chain("action-nope", store=director.store) is None


def test_event_action_id_resolves_only_action_events(director) -> None:
    executed = _approve_garage(director)
    executed_event = next(
        e for e in director.store.events if e.event_type.value == "action_executed"
    )
    assert event_action_id(director.store, executed_event.event_id) == executed.action_id

    rule_event = next(e for e in director.store.events if e.event_type.value == "rule_proposed")
    assert event_action_id(director.store, rule_event.event_id) is None
    assert event_action_id(director.store, "event-nope") is None


# -- HTTP surface ---------------------------------------------------------------


@pytest.fixture()
def server():
    instance, director = make_server(0, clock=lambda: FIXED_NOW, demo=True)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, director, instance.server_address[1]
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


def _run_demo_action_over_http(port: int) -> str:
    """Approve the pending garage close; return the executed action's event id."""
    _, state = _get_json(port, "/api/state")
    (pending,) = state["pending"]
    _post(port, f"/api/requests/{pending['request_id']}/approve", {})
    _, state = _get_json(port, "/api/state")
    return next(row["event_id"] for row in state["activity"] if row["event_type"] == "action_executed")


def test_chain_routes_over_http(server) -> None:
    _, _, port = server
    event_id = _run_demo_action_over_http(port)

    status, body = _get_json(port, f"/api/actions/chain?event_id={event_id}")
    assert status == 200
    assert body["ok"] is True
    chain = body["chain"]
    for section in (
        "request", "interpretation", "evidence", "authority", "execution", "consequence", "timeline"
    ):
        assert section in chain, section
    assert chain["request"]["target_device_id"] == "garage_door"
    assert chain["authority"]["status"] == "allow"
    assert chain["authority"]["code"] == "allowed"
    assert chain["execution"]["service"] == "cover.close"
    assert chain["consequence"]["observed"] is True
    assert chain["consequence"]["delay_ms"] is not None
    assert chain["timeline"]

    # The canonical action-id route agrees with the event-id resolution.
    status, by_id = _get_json(port, f"/api/actions/{chain['action_id']}/chain")
    assert status == 200
    assert by_id == body


def test_chain_routes_fail_closed_on_unknown_ids(server) -> None:
    _, _, port = server
    _run_demo_action_over_http(port)

    status, body = _get_json(port, "/api/actions/action-nope/chain")
    assert status == 200
    assert body["ok"] is False

    status, body = _get_json(port, "/api/actions/chain?event_id=event-nope")
    assert status == 200
    assert body["ok"] is False

    # A rule event has no action correlation: no drill-down, honest refusal.
    _, state = _get_json(port, "/api/state")
    rule_row = next(row for row in state["activity"] if row["event_type"] == "rule_proposed")
    status, body = _get_json(port, f"/api/actions/chain?event_id={rule_row['event_id']}")
    assert status == 200
    assert body["ok"] is False

    status, body = _get_json(port, "/api/actions/chain")
    assert status == 200
    assert body["ok"] is False
