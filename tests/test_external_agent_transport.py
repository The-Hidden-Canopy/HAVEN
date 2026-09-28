"""TransportBridge: the one place a transport's tool call becomes admission + dispatch.

Covers credential resolution (unknown/blank credentials deny identically --
no oracle), the tool registry (unknown tools, malformed arguments), and the
read/action dispatch split. The authority path itself is covered by
`test_external_agent_actions.py`; here a stub executor proves the bridge
hands the admitted principal and provenance through untouched.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import (
    ExternalAgentConnection,
    ExternalProvider,
    ExternalScope,
    PrincipalBinding,
)
from haven.external_agents.errors import (
    CONNECTION_DISABLED,
    CONNECTION_UNKNOWN,
    INVALID_ARGUMENTS,
    SCOPE_MISSING,
    ExternalDenied,
)
from haven.external_agents.gateway import ExternalAgentGateway
from haven.external_agents.reads import READ_TOOL_SCOPES, ExternalReadTools
from haven.external_agents.store import ExternalAgentStore
from haven.external_agents.transport import ACTION_TOOL_SCOPES, TOOL_SCOPES, TransportBridge, hash_credential

HOUSEHOLD = "household-1"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
CREDENTIAL = "test-bearer-token"


class _StubReader:
    def state(self):
        return {"rooms": [{"id": "office", "devices": [], "people": []}]}


class _StubExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def request_action_as(self, principal, device_id, service, parameters=None, *, external_connection_id=None, external_source=None):
        self.calls.append(("request", principal, device_id, service, external_connection_id, external_source))
        return {"ok": True, "status": "executed"}

    def confirm_pending_as(self, principal, request_id, *, external_source=None):
        self.calls.append(("confirm", principal, request_id))
        return {"ok": True, "status": "confirmed"}

    def deny_pending_as(self, principal, request_id):
        self.calls.append(("deny", principal, request_id))
        return {"ok": True, "status": "denied"}


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ExternalAgentStore(Path(tmp) / "external_agents.db")


def _bridge(store, executor=None, resolve_principal=None):
    gateway = ExternalAgentGateway(
        store=store,
        household_id=HOUSEHOLD,
        resolve_principal=resolve_principal or (lambda _pid: None),
        clock=lambda: NOW,
    )
    return TransportBridge(
        store=store,
        gateway=gateway,
        reads=ExternalReadTools(gateway=gateway, reader=_StubReader()),
        executor=executor or _StubExecutor(),
    )


def _connection(store, **overrides) -> ExternalAgentConnection:
    defaults = dict(
        connection_id="extconn-1",
        household_id=HOUSEHOLD,
        provider=ExternalProvider.MCP_CLIENT,
        display_name="MCP client",
        enabled=True,
        created_at=NOW,
        created_by="owner-1",
        credential_hash=hash_credential(CREDENTIAL),
    )
    defaults.update(overrides)
    connection = ExternalAgentConnection(**defaults)
    store.save_connection(connection)
    return connection


def _call(bridge, **overrides):
    params = dict(
        credential=CREDENTIAL,
        tool="haven.world.get",
        external_request_id="mcp_rpc_1",
    )
    params.update(overrides)
    return bridge.call_tool(**params)


def test_unknown_credential_is_denied_like_an_unknown_connection(store):
    _connection(store)

    with pytest.raises(ExternalDenied) as exc:
        _call(_bridge(store), credential="wrong-token")

    assert exc.value.code == CONNECTION_UNKNOWN


def test_blank_credential_denies_without_an_oracle(store):
    _connection(store)

    with pytest.raises(ExternalDenied) as exc:
        _call(_bridge(store), credential="  ")

    assert exc.value.code == CONNECTION_UNKNOWN


def test_unknown_tool_is_invalid_arguments(store):
    _connection(store)

    with pytest.raises(ExternalDenied) as exc:
        _call(_bridge(store), tool="haven.world.delete")

    assert exc.value.code == INVALID_ARGUMENTS


def test_missing_request_id_is_invalid_arguments(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))

    with pytest.raises(ExternalDenied) as exc:
        _call(_bridge(store), external_request_id="")

    assert exc.value.code == INVALID_ARGUMENTS


def test_read_tool_dispatches_through_admission(store):
    _connection(store, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))

    result = _call(_bridge(store))

    assert result["ok"] is True
    assert result["state"]["rooms"][0]["id"] == "office"


def test_read_tool_denied_when_the_scope_is_not_granted(store):
    _connection(store, unbound_scopes=frozenset())

    with pytest.raises(ExternalDenied) as exc:
        _call(_bridge(store))

    assert exc.value.code == SCOPE_MISSING


def test_disabled_connection_with_a_valid_credential_is_denied(store):
    _connection(store, enabled=False, unbound_scopes=frozenset({ExternalScope.WORLD_READ}))

    with pytest.raises(ExternalDenied) as exc:
        _call(_bridge(store))

    assert exc.value.code == CONNECTION_DISABLED


def test_action_request_reaches_the_executor_as_the_admitted_principal(store):
    executor = _StubExecutor()
    bridge = _bound_bridge(store, executor)

    result = _call(
        bridge,
        tool="haven.action.request",
        subject="subj-1",
        arguments={"device_id": "light.office", "service": "light.turn_off"},
    )

    assert result == {"ok": True, "status": "executed"}
    (kind, principal, device_id, service, connection_id, external_source) = executor.calls[0]
    assert kind == "request"
    assert principal.actor_id == "person-gerron"
    assert (device_id, service) == ("light.office", "light.turn_off")
    assert connection_id == "extconn-1"
    assert ("provider", "mcp_client") in external_source


def test_external_request_id_retries_are_idempotent_and_conflicts_fail(store):
    executor = _StubExecutor()
    bridge = _bound_bridge(store, executor)
    arguments = {"device_id": "light.office", "service": "light.turn_off"}

    first = _call(
        bridge,
        tool="haven.action.request",
        external_request_id="action-retry-1",
        subject="subj-1",
        arguments=arguments,
    )
    retry = _call(
        bridge,
        tool="haven.action.request",
        external_request_id="action-retry-1",
        subject="subj-1",
        arguments=arguments,
    )
    assert retry == first
    assert [call[0] for call in executor.calls] == ["request"]

    with pytest.raises(ExternalDenied) as exc:
        _call(
            bridge,
            tool="haven.action.request",
            external_request_id="action-retry-1",
            subject="subj-1",
            arguments={"device_id": "light.bedroom", "service": "light.turn_off"},
        )
    assert exc.value.code == INVALID_ARGUMENTS
    assert "different request" in exc.value.message


def _bound_bridge(store, executor: _StubExecutor) -> TransportBridge:
    """A bridge whose caller binds to the household owner with actions.request.

    Unbound subjects can only ever hold read scopes, so the action path is
    always exercised through a binding.
    """

    _connection(store)
    store.save_binding(
        PrincipalBinding(
            binding_id="bind-1",
            connection_id="extconn-1",
            subject_key="subj-1",
            subject_label="Gerron's voice",
            principal_id="person-gerron",
            scopes=frozenset({ExternalScope.ACTIONS_REQUEST}),
            created_by="owner-1",
            created_at=NOW,
        )
    )
    resident = Principal(actor_id="person-gerron", household_id=HOUSEHOLD, role_tier=RoleTier.OWNER)
    return _bridge(store, executor=executor, resolve_principal={"person-gerron": resident}.get)


def test_action_request_validates_arguments_before_dispatch(store):
    executor = _StubExecutor()
    bridge = _bound_bridge(store, executor)

    with pytest.raises(ExternalDenied) as exc:
        _call(bridge, tool="haven.action.request", subject="subj-1", arguments={"service": "x"})

    assert exc.value.code == INVALID_ARGUMENTS
    assert executor.calls == []


def test_action_confirm_and_deny_dispatch(store):
    executor = _StubExecutor()
    bridge = _bound_bridge(store, executor)

    confirmed = _call(bridge, tool="haven.action.confirm", subject="subj-1", arguments={"request_id": "req-9"})
    denied = _call(
        bridge,
        tool="haven.action.deny",
        external_request_id="mcp_rpc_2",
        subject="subj-1",
        arguments={"request_id": "req-9"},
    )

    assert confirmed["status"] == "confirmed"
    assert denied["status"] == "denied"
    assert [call[0] for call in executor.calls] == ["confirm", "deny"]


def test_tool_scopes_is_the_read_and_action_registries_combined():
    assert TOOL_SCOPES == {**READ_TOOL_SCOPES, **ACTION_TOOL_SCOPES}
    assert set(ACTION_TOOL_SCOPES.values()) == {ExternalScope.ACTIONS_REQUEST}
