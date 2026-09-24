"""ScopeStore: scope/membership persistence and visibility derivation."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.scopes.models import Membership, ScopeRef
from haven.scopes.store import ScopeStore

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ScopeStore(Path(tmp) / "scopes.db")


def test_scope_round_trip(store) -> None:
    scope = ScopeRef(
        scope_id="scope:personal",
        kind="personal",
        name="Personal",
        created_at=NOW,
        status="active",
        policy_ref="policy:local",
    )
    store.save_scope(scope)
    assert store.get_scope("scope:personal") == scope
    assert [s.scope_id for s in store.list_scopes()] == ["scope:personal"]


def test_scope_validation(store) -> None:
    with pytest.raises(ValueError):
        ScopeRef(scope_id="  ", kind="personal", name="Personal")
    with pytest.raises(ValueError):
        ScopeRef(scope_id="s", kind="personal", name="Personal", created_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        store.save_scope(ScopeRef(scope_id="s", kind="personal", name="  "))


def test_membership_round_trip_and_validity_windows(store) -> None:
    store.save_scope(ScopeRef(scope_id="scope:a", kind="personal", name="A"))
    store.save_scope(ScopeRef(scope_id="scope:b", kind="workspace", name="B"))
    store.add_membership(
        Membership(
            principal_id="principal:1",
            scope_id="scope:a",
            role="owner",
            capabilities=("administer", "write"),
            valid_from=NOW - timedelta(days=1),
        )
    )
    store.add_membership(
        Membership(
            principal_id="principal:1",
            scope_id="scope:b",
            role="member",
            valid_from=NOW - timedelta(days=2),
            valid_until=NOW - timedelta(days=1),
        )
    )
    memberships = store.memberships_for("principal:1", now=NOW)
    assert [m.scope_id for m in memberships] == ["scope:a"]
    assert memberships[0].role == "owner"
    assert memberships[0].capabilities == ("administer", "write")
    # Before the validity window opens, the membership grants nothing.
    assert store.memberships_for("principal:1", now=NOW - timedelta(days=3)) == ()
    # A different principal has no standing.
    assert store.memberships_for("principal:2", now=NOW) == ()


def test_visible_scope_ids_derive_from_memberships_only(store) -> None:
    store.save_scope(ScopeRef(scope_id="scope:personal", kind="personal", name="Personal"))
    store.save_scope(ScopeRef(scope_id="scope:home", kind="household", name="Home",
                              parent_scope_id="scope:personal"))
    store.save_scope(ScopeRef(scope_id="scope:secret", kind="project", name="Secret"))
    store.add_membership(Membership(principal_id="p", scope_id="scope:home", role="member",
                                    valid_from=NOW))
    store.add_membership(Membership(principal_id="p", scope_id="scope:personal", role="owner",
                                    valid_from=NOW))
    # Hierarchy does not imply inheritance: membership in the child does not
    # widen anything, and the unmembership'd project is invisible even though
    # it is just another row in the same store.
    assert store.visible_scope_ids("p", now=NOW) == ("scope:home", "scope:personal")
    # Upsert replaces the membership (same primary key).
    store.add_membership(Membership(principal_id="p", scope_id="scope:home", role="member",
                                    valid_from=NOW, valid_until=NOW + timedelta(days=1)))
    assert store.visible_scope_ids("p", now=NOW + timedelta(days=2)) == ("scope:personal",)


def test_legacy_mapping_round_trip(store) -> None:
    store.record_legacy_mapping(
        legacy_scope_id="household-old",
        canonical_scope_id="scope:personal",
        note="computer data moved to the personal root",
        at=NOW,
    )
    (mapping,) = store.legacy_mappings()
    assert mapping["legacy_scope_id"] == "household-old"
    assert mapping["canonical_scope_id"] == "scope:personal"
    assert "personal root" in mapping["note"]
    store.remove_legacy_mapping("household-old")
    assert store.legacy_mappings() == ()
