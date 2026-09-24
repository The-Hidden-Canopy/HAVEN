"""Household-first migration: personal-root layout, legacy mapping, rollback.

Fixture shape: a data dir whose resources/claims were written under the
household id *before* scopes existed -- the exact state an upgraded
installation boots with. Provisioning + migration then run over it, the
way `HavenWebServer.__init__` does on boot.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.identity import provision_identity
from haven.knowledge import Claim, ClaimProvenance, ClaimState, ClaimStore
from haven.resources import ResourceRecord, ResourceStore
from haven.scopes.migration import (
    migrate_household_first_installation,
    rollback_migration,
)
from haven.scopes.store import ScopeStore
from haven.web.setup_config import SetupConfig, SetupConfigStore

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
HOUSEHOLD = "household-authoring"


def _clock() -> datetime:
    return NOW


@pytest.fixture()
def household_first():
    """A data dir in the pre-scope layout: config + household resources and
    claims keyed to the household id, no identity.json or scopes.db yet."""
    tmp = tempfile.TemporaryDirectory()
    data_dir = Path(tmp.name) / "data"
    data_dir.mkdir(parents=True)
    SetupConfigStore(data_dir / "haven.json").save(
        SetupConfig(completed=True, data_dir=str(data_dir), household_id=HOUSEHOLD)
    )
    resources = ResourceStore(data_dir / "resources.db")
    resources.save(
        ResourceRecord(
            resource_id="file:notes",
            resource_type="file",
            scope_id=HOUSEHOLD,
            provider_id="local_filesystem",
            title="personal notes",
            locator="E:/notes/todo.txt",
            capabilities=(),
            observed_at=NOW,
        )
    )
    resources.save(
        ResourceRecord(
            resource_id="device:thermostat",
            resource_type="device",
            scope_id=HOUSEHOLD,
            provider_id="home_assistant",
            title="Thermostat",
            locator=None,
            capabilities=(),
            observed_at=NOW,
        )
    )
    claims = ClaimStore(data_dir / "claims.db")
    claims.save(
        Claim(
            claim_id="claim:from-notes",
            scope_id=HOUSEHOLD,
            proposition="The notes mention lunar preparation.",
            state=ClaimState.REPORTED,
            source_refs=("file:notes",),
            evidence_refs=("file:notes#paragraph:1",),
            created_at=NOW,
            confidence=0.9,
            provenance=ClaimProvenance.DOCUMENT_STATED,
        )
    )
    claims.save(
        Claim(
            claim_id="claim:home",
            scope_id=HOUSEHOLD,
            proposition="The thermostat is set to 21 degrees.",
            state=ClaimState.REPORTED,
            source_refs=("device:thermostat",),
            evidence_refs=(),
            created_at=NOW,
            confidence=1.0,
            provenance=ClaimProvenance.DIRECT_OBSERVATION,
        )
    )
    scope_store = ScopeStore(data_dir / "scopes.db")
    identity, created = provision_identity(
        data_dir=data_dir, household_id=HOUSEHOLD, scope_store=scope_store, clock=_clock
    )
    report = migrate_household_first_installation(
        scope_store=scope_store,
        identity=identity,
        resources=resources,
        claims=claims,
        clock=_clock,
    )
    yield data_dir, resources, claims, scope_store, identity, report, created
    tmp.cleanup()


def test_migration_parents_household_and_moves_computer_data(household_first) -> None:
    data_dir, resources, claims, scope_store, identity, report, created = household_first
    assert created is True
    personal = identity.personal_scope_id

    household = scope_store.get_scope(HOUSEHOLD)
    assert household.kind == "household"
    assert household.parent_scope_id == personal
    assert scope_store.get_scope(personal).kind == "personal"

    # Computer resource moved; home device stayed home-specific.
    assert resources.get("file:notes").scope_id == personal
    assert resources.get("device:thermostat").scope_id == HOUSEHOLD
    # Claim built on the moved resource moved with it; the home claim stayed.
    assert claims.get("claim:from-notes").scope_id == personal
    assert claims.get("claim:home").scope_id == HOUSEHOLD

    assert {row[0] for row in report.migrated_resources} == {"file:notes"}
    assert {row[0] for row in report.migrated_claims} == {"claim:from-notes"}
    assert report.moved_anything is True


def test_migration_writes_auditable_legacy_mapping(household_first) -> None:
    _data_dir, _resources, _claims, scope_store, identity, report, _created = household_first
    (mapping,) = scope_store.legacy_mappings()
    assert mapping["legacy_scope_id"] == HOUSEHOLD
    assert mapping["canonical_scope_id"] == identity.personal_scope_id
    assert "legacy child scope" in mapping["note"]
    # Receipts keep their original scope ids; the mapping is what keeps a
    # pre-migration receipt that names the household id interpretable.
    assert report.legacy_mapping_note is not None


def test_migration_preserves_record_content_and_history(household_first) -> None:
    _data_dir, _resources, claims, _store, identity, _report, _created = household_first
    moved = claims.get("claim:from-notes")
    assert moved.scope_id == identity.personal_scope_id
    assert moved.proposition == "The notes mention lunar preparation."
    assert moved.source_refs == ("file:notes",)
    assert moved.evidence_refs == ("file:notes#paragraph:1",)
    assert moved.created_at == NOW
    assert moved.confidence == 0.9


def test_migration_is_idempotent(household_first) -> None:
    data_dir, resources, claims, scope_store, identity, first, _created = household_first
    second = migrate_household_first_installation(
        scope_store=scope_store,
        identity=identity,
        resources=resources,
        claims=claims,
        clock=_clock,
    )
    assert second.moved_anything is False
    assert second.migrated_resources == ()
    assert resources.get("file:notes").scope_id == identity.personal_scope_id
    # One mapping row, not one per boot.
    assert len(scope_store.legacy_mappings()) == 1


def test_rollback_restores_household_layout(household_first) -> None:
    _data_dir, resources, claims, scope_store, identity, report, _created = household_first
    rollback_migration(report, scope_store=scope_store, resources=resources, claims=claims)
    assert resources.get("file:notes").scope_id == HOUSEHOLD
    assert claims.get("claim:from-notes").scope_id == HOUSEHOLD
    # The home-specific rows were never moved; rollback leaves them alone.
    assert claims.get("claim:home").scope_id == HOUSEHOLD
    assert scope_store.legacy_mappings() == ()
    # The scope layout itself (personal root + parented household) survives
    # the rollback: only the data moves back.
    assert scope_store.get_scope(HOUSEHOLD).parent_scope_id == identity.personal_scope_id


def test_migration_then_new_boot_is_a_noop(household_first) -> None:
    """Restart durability: a second boot over the migrated dir moves nothing
    and reads the same principal."""
    from haven.identity import LocalIdentityProvider

    data_dir, resources, claims, scope_store, identity, _report, _created = household_first
    server_tmp = data_dir
    rebound = LocalIdentityProvider(
        path=server_tmp / "identity.json",
        household_id=HOUSEHOLD,
        scope_store=scope_store,
        clock=_clock,
    )
    assert rebound.principal_id == identity.principal_id
    again = migrate_household_first_installation(
        scope_store=scope_store,
        identity=rebound,
        resources=resources,
        claims=claims,
        clock=_clock,
    )
    assert again.moved_anything is False
