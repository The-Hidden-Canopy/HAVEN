"""External scope model: presets, parsing, and the unbound-scope ceiling.

Scope intersection (role + scope both required, scope alone cannot promote
a resident to owner) is exercised in test_external_agent_gateway.py against
the live admission path; this file is about the scope vocabulary itself.
"""

from datetime import datetime, timezone

import pytest

from haven.external_agents.domain import (
    MUTATING_SCOPES,
    SCOPE_PRESETS,
    UNBOUND_GRANTABLE_SCOPES,
    ExternalAgentConnection,
    ExternalProvider,
    ExternalScope,
    parse_scopes,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def test_every_scope_preset_is_built_from_real_scope_values():
    for name, scopes in SCOPE_PRESETS.items():
        assert scopes, f"preset {name} is empty"
        for scope in scopes:
            assert isinstance(scope, ExternalScope)


def test_presets_are_additive_read_only_to_trusted():
    assert SCOPE_PRESETS["read_only"] <= SCOPE_PRESETS["assistant"]
    assert SCOPE_PRESETS["assistant"] <= SCOPE_PRESETS["trusted_resident"]
    assert SCOPE_PRESETS["trusted_resident"] <= SCOPE_PRESETS["owner_external"]


def test_connections_manage_is_never_in_a_named_preset():
    """Connection management is reserved for an explicit owner-only local
    flow (spec 8.2), never a conversational scope preset a binding grants."""

    for scopes in SCOPE_PRESETS.values():
        assert ExternalScope.CONNECTIONS_MANAGE not in scopes


def test_mutating_scopes_cover_every_scope_that_can_change_state():
    for scope in (
        ExternalScope.KNOWLEDGE_CORRECT,
        ExternalScope.ACTIONS_REQUEST,
        ExternalScope.AUTOMATIONS_PROPOSE,
        ExternalScope.AUTOMATIONS_APPROVE,
        ExternalScope.CONNECTIONS_MANAGE,
    ):
        assert scope in MUTATING_SCOPES
    for scope in (ExternalScope.WORLD_READ, ExternalScope.KNOWLEDGE_READ, ExternalScope.HISTORY_READ):
        assert scope not in MUTATING_SCOPES


def test_parse_scopes_accepts_known_string_values():
    parsed = parse_scopes(["world.read", "actions.request"])

    assert parsed == frozenset({ExternalScope.WORLD_READ, ExternalScope.ACTIONS_REQUEST})


def test_parse_scopes_rejects_an_unknown_value():
    with pytest.raises(ValueError):
        parse_scopes(["world.read", "owner.role"])


def test_parse_scopes_rejects_a_bare_string_instead_of_a_list():
    with pytest.raises(ValueError):
        parse_scopes("world.read")


def test_connection_unbound_scopes_may_not_exceed_the_grantable_ceiling():
    with pytest.raises(ValueError):
        ExternalAgentConnection(
            connection_id="c",
            household_id="h",
            provider=ExternalProvider.ALEXA_PLUS,
            display_name="Alexa+",
            enabled=True,
            created_at=NOW,
            created_by="owner-1",
            unbound_scopes=frozenset({ExternalScope.ACTIONS_REQUEST}),
        )


def test_connection_unbound_scopes_within_the_ceiling_is_accepted():
    connection = ExternalAgentConnection(
        connection_id="c",
        household_id="h",
        provider=ExternalProvider.ALEXA_PLUS,
        display_name="Alexa+",
        enabled=True,
        created_at=NOW,
        created_by="owner-1",
        unbound_scopes=UNBOUND_GRANTABLE_SCOPES,
    )

    assert connection.unbound_scopes == UNBOUND_GRANTABLE_SCOPES


def test_unbound_grantable_scopes_are_all_read_scopes():
    for scope in UNBOUND_GRANTABLE_SCOPES:
        assert scope not in MUTATING_SCOPES
