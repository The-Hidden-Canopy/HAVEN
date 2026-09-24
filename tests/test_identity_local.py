"""LocalIdentityProvider: first-run provisioning, persistence, memberships."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.core.domain import RoleTier
from haven.identity import LocalIdentityProvider, provision_identity
from haven.scopes.models import Membership
from haven.scopes.store import ScopeStore

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
HOUSEHOLD = "household-authoring"


@pytest.fixture()
def scope_store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ScopeStore(Path(tmp) / "scopes.db")


def _clock() -> datetime:
    return NOW


def test_first_run_mints_and_provisions_personal_root(scope_store, tmp_path) -> None:
    provider, created = provision_identity(
        data_dir=tmp_path, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    assert created is True
    assert provider.principal_id.startswith("principal:")
    assert provider.personal_scope_id.startswith("scope:")

    personal = scope_store.get_scope(provider.personal_scope_id)
    assert personal is not None
    assert personal.kind == "personal"
    assert personal.parent_scope_id is None

    household = scope_store.get_scope(HOUSEHOLD)
    assert household is not None
    assert household.kind == "household"
    assert household.parent_scope_id == provider.personal_scope_id

    assert provider.visible_scope_ids() == tuple(sorted((HOUSEHOLD, provider.personal_scope_id)))
    roles = {m.scope.scope_id: m.role for m in provider.memberships()}
    assert roles[provider.personal_scope_id] == "owner"
    assert roles[HOUSEHOLD] == "member"


def test_provisioning_is_idempotent(scope_store, tmp_path) -> None:
    provider, created = provision_identity(
        data_dir=tmp_path, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    again, created_again = provision_identity(
        data_dir=tmp_path, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    assert created is True and created_again is False
    assert again.principal_id == provider.principal_id
    assert [s.scope_id for s in scope_store.list_scopes()] == sorted(
        (HOUSEHOLD, provider.personal_scope_id)
    )


def test_identity_is_restart_durable(scope_store, tmp_path) -> None:
    provider, _ = provision_identity(
        data_dir=tmp_path, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    rebound = LocalIdentityProvider(
        path=tmp_path / "identity.json",
        household_id=HOUSEHOLD,
        scope_store=scope_store,
        clock=_clock,
    )
    assert rebound.principal_id == provider.principal_id
    assert rebound.personal_scope_id == provider.personal_scope_id


def test_partial_identity_file_is_rejected_not_interpreted(scope_store, tmp_path) -> None:
    (tmp_path / "identity.json").write_text(
        json.dumps({"principal_id": "principal:x"}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        LocalIdentityProvider(
            path=tmp_path / "identity.json",
            household_id=HOUSEHOLD,
            scope_store=scope_store,
            clock=_clock,
        )


def test_memberships_exclude_expired_and_inactive_scopes(scope_store, tmp_path) -> None:
    provider, _ = provision_identity(
        data_dir=tmp_path, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    scope_store.add_membership(
        Membership(
            principal_id=provider.principal_id,
            scope_id=HOUSEHOLD,
            role="member",
            valid_from=NOW - timedelta(days=2),
            valid_until=NOW - timedelta(days=1),
        )
    )
    assert provider.memberships(now=NOW) == tuple(
        m for m in provider.memberships(now=NOW) if m.scope.scope_id != HOUSEHOLD
    )
    assert provider.visible_scope_ids(now=NOW) == (provider.personal_scope_id,)


def test_current_principal_reuses_the_governed_loop_actor_type(scope_store, tmp_path) -> None:
    provider, _ = provision_identity(
        data_dir=tmp_path, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    principal = provider.current_principal()
    assert principal.actor_id == provider.principal_id
    assert principal.household_id == HOUSEHOLD
    assert principal.role_tier is RoleTier.OWNER
