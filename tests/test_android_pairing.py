"""Synthetic owner pairing tests use the real external-agent store and gateway."""
from datetime import datetime, timedelta, timezone

import pytest

from haven.android_bridge import dispatch
from haven.android_bridge.pairing import PairingCoordinator, PairingRegistry, PairedSubjects, proof_digest, subject_for
from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import ExternalScope
from haven.external_agents.gateway import ExternalAgentGateway, ExternalAgentService
from haven.external_agents.reads import ExternalReadTools
from haven.external_agents.store import ExternalAgentStore
from haven.external_agents.transport import TransportBridge, hash_credential
from haven.web.demo import DemoDirector, HOUSEHOLD_ID

NOW = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
VERIFIER = "V" * 43  # synthetic test fixture, not an actual credential
TOKEN = "T" * 43

@pytest.fixture
def paired(tmp_path):
    now = [NOW]
    clock = lambda: now[0]
    director = DemoDirector(clock=clock, scenario=False)
    owner = Principal(actor_id="owner", household_id=HOUSEHOLD_ID, role_tier=RoleTier.OWNER)
    member = Principal(actor_id="member", household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER)
    people = {"owner": owner, "member": member}
    current = [owner]
    store = ExternalAgentStore(tmp_path / "external.db")
    service = ExternalAgentService(store=store, household_id=HOUSEHOLD_ID, clock=clock)
    gateway = ExternalAgentGateway(store=store, household_id=HOUSEHOLD_ID, resolve_principal=people.get, clock=clock)
    bridge = TransportBridge(store=store, gateway=gateway, reads=ExternalReadTools(gateway=gateway, reader=director), executor=director)
    registry = PairingRegistry(tmp_path / "phone-gate.db")
    coordinator = PairingCoordinator(household_id=HOUSEHOLD_ID, external_service=service, store=store, registry=registry,
        current_owner=lambda: current[0], resolve_principal=people.get, issuer=lambda: TOKEN, clock=clock)
    subjects = PairedSubjects(store, HOUSEHOLD_ID, registry, clock=clock)
    return coordinator, subjects, bridge, store, now, people, current


def request(coordinator):
    return coordinator.request("Test phone", proof_digest(VERIFIER))


def approve(coordinator, req, scopes=None):
    return coordinator.approve(req["request_id"], req["comparison_code"], "member",
        scopes or {ExternalScope.WORLD_READ}, NOW + timedelta(days=1))


def read(bridge, subjects, token, request_id="read-1"):
    return dispatch(bridge, token, subjects, {"tool": "haven.world.get", "request_id": request_id, "arguments": {}})


def test_owner_approval_proof_single_use_reconnect_and_revocation(paired):
    coordinator, subjects, bridge, store, now, people, current = paired
    req = request(coordinator)
    with pytest.raises(PermissionError): coordinator.redeem(req["request_id"], VERIFIER)
    approve(coordinator, req)
    with pytest.raises(PermissionError): coordinator.redeem(req["request_id"], "W" * 43)
    issued = coordinator.redeem(req["request_id"], VERIFIER)
    with pytest.raises(PermissionError): coordinator.redeem(req["request_id"], VERIFIER)
    connection = store.get_connection(issued["connection_id"])
    assert connection.active and connection.credential_hash == hash_credential(TOKEN)
    assert not connection.unbound_scopes
    assert store.active_binding(connection.connection_id, subject_for(connection.connection_id)).principal_id == "member"
    assert read(bridge, subjects, TOKEN)[0] == 200
    # A new resolver instance models host restart; no secret mapping persisted.
    assert read(bridge, PairedSubjects(store, HOUSEHOLD_ID, coordinator.registry, clock=lambda: now[0]), TOKEN, "read-2")[0] == 200
    coordinator.revoke(issued["connection_id"])
    assert read(bridge, subjects, TOKEN, "read-3")[0] == 401
    assert any(row["kind"] == "connection.revoked" for row in store.audit(HOUSEHOLD_ID))


def test_binding_expiry_fails_closed_and_requires_new_pairing(paired):
    coordinator, subjects, bridge, store, now, people, current = paired
    req = request(coordinator); approve(coordinator, req)
    coordinator.redeem(req["request_id"], VERIFIER)
    now[0] = NOW + timedelta(days=1)
    assert read(bridge, subjects, TOKEN)[0] == 401


def test_untrusted_owner_scope_code_and_principal_are_rejected(paired):
    coordinator, subjects, bridge, store, now, people, current = paired
    req = request(coordinator)
    current[0] = people["member"]
    with pytest.raises(PermissionError): approve(coordinator, req)
    current[0] = Principal(actor_id="owner", household_id="other", role_tier=RoleTier.OWNER)
    with pytest.raises(PermissionError): approve(coordinator, req)
    current[0] = people["owner"]
    with pytest.raises(ValueError): coordinator.approve(req["request_id"], "wrong", "member", {ExternalScope.WORLD_READ}, NOW + timedelta(days=1))
    with pytest.raises(ValueError): coordinator.approve(req["request_id"], req["comparison_code"], "member", {ExternalScope.CONNECTIONS_MANAGE}, NOW + timedelta(days=1))
    with pytest.raises(ValueError): coordinator.approve(req["request_id"], req["comparison_code"], "unknown", {ExternalScope.WORLD_READ}, NOW + timedelta(days=1))
    assert not store.list_connections(HOUSEHOLD_ID)


def test_request_expiry_owner_removal_and_no_default_issuer(paired):
    coordinator, subjects, bridge, store, now, people, current = paired
    req = request(coordinator); approve(coordinator, req)
    coordinator.issuer = None
    with pytest.raises(RuntimeError): coordinator.redeem(req["request_id"], VERIFIER)
    coordinator.issuer = lambda: TOKEN
    people.pop("owner")
    with pytest.raises(PermissionError): coordinator.redeem(req["request_id"], VERIFIER)
    people["owner"] = current[0]
    now[0] = NOW + timedelta(minutes=5)
    with pytest.raises(PermissionError): coordinator.redeem(req["request_id"], VERIFIER)
    assert not store.list_connections(HOUSEHOLD_ID)


def test_partial_issuance_is_revoked(paired, monkeypatch):
    coordinator, subjects, bridge, store, now, people, current = paired
    req = request(coordinator); approve(coordinator, req)
    monkeypatch.setattr(coordinator.service, "upsert_binding", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fixture failure")))
    with pytest.raises(RuntimeError): coordinator.redeem(req["request_id"], VERIFIER)
    connections = store.list_connections(HOUSEHOLD_ID)
    assert len(connections) == 1 and not connections[0].active
    assert read(bridge, subjects, TOKEN)[0] == 401


def test_world_only_connection_cannot_request_action(paired):
    coordinator, subjects, bridge, store, now, people, current = paired
    req = request(coordinator); approve(coordinator, req)
    coordinator.redeem(req["request_id"], VERIFIER)
    status, response = dispatch(bridge, TOKEN, subjects, {"tool": "haven.action.request", "request_id": "action-1", "arguments": {"device_id": "office_light", "service": "light.turn_off"}})
    assert status == 403 and response["code"] == "external.scope_missing"


def test_pairing_activation_gate_stays_closed_if_enable_fails_after_write(paired, monkeypatch):
    coordinator, subjects, bridge, store, now, people, current = paired
    req=request(coordinator); approve(coordinator, req)
    original=coordinator.service.set_connection_enabled
    def fail_after_enable(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("audit-like failure after enable")
    monkeypatch.setattr(coordinator.service, "set_connection_enabled", fail_after_enable)
    monkeypatch.setattr(coordinator.service, "revoke_connection", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("revocation failed")))
    with pytest.raises(RuntimeError): coordinator.redeem(req["request_id"], VERIFIER)
    connection=store.list_connections(HOUSEHOLD_ID)[0]
    assert connection.active  # existing store needs owner follow-up
    assert not coordinator.registry.is_active(connection.connection_id)
    assert read(bridge, subjects, TOKEN)[0] == 401


def test_revocation_closes_gate_even_if_existing_service_fails(paired, monkeypatch):
    coordinator, subjects, bridge, store, now, people, current = paired
    req=request(coordinator); approve(coordinator, req); issued=coordinator.redeem(req["request_id"], VERIFIER)
    monkeypatch.setattr(coordinator.service, "revoke_connection", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("audit-like failure")))
    with pytest.raises(RuntimeError): coordinator.revoke(issued["connection_id"])
    assert store.get_connection(issued["connection_id"]).active
    assert read(bridge, subjects, TOKEN)[0] == 401
