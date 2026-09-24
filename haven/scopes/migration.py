"""Household-first migration to the personal-root scope layout.

An installation that predates scopes keyed everything — resources,
claims, receipts — to its household id. Migration (design spec page 19):

  * the personal root scope already exists (provisioning created it and
    parented the household beneath it, keeping the household's id);
  * computer-provider resources and the claims built on them move to the
    personal root, unless they are explicitly home-specific (home-provider
    objects stay in the household scope);
  * the move is recorded in the scope store's auditable legacy mapping so
    historical receipts that still name the household id stay
    interpretable: legacy household id -> personal root for migrated
    computer data, with the household scope retained as a legacy child.

`rollback_migration` reverses a report exactly: every moved record goes
back to its old scope id and the mapping row is removed. Both directions
are explicit and tested; the migration itself is idempotent (a second run
finds nothing left in the household scope to move).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from haven.identity.local import LocalIdentityProvider
    from haven.knowledge.store import ClaimStore
    from haven.resources.store import ResourceStore
    from haven.scopes.store import ScopeStore

# Providers whose resources are "the computer" rather than "the home".
# `local_computer` is the id the test substrate and early receipts used;
# `local_filesystem` is the built-in provider's own id.
COMPUTER_PROVIDER_IDS = frozenset({"local_filesystem", "local_computer"})
HOME_PROVIDER_IDS = frozenset({"home_assistant", "demo.house"})


@dataclass(frozen=True)
class MigrationReport:
    """What one migration run did; the rollback input."""

    household_scope_id: str
    personal_scope_id: str
    migrated_resources: tuple[tuple[str, str, str], ...] = ()  # (id, old_scope, new_scope)
    migrated_claims: tuple[tuple[str, str, str], ...] = ()
    legacy_mapping_note: str | None = None

    @property
    def moved_anything(self) -> bool:
        return bool(self.migrated_resources or self.migrated_claims)


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def migrate_household_first_installation(
    *,
    scope_store: ScopeStore,
    identity: LocalIdentityProvider,
    resources: ResourceStore,
    claims: ClaimStore,
    clock=_default_clock,
    computer_provider_ids: frozenset[str] = COMPUTER_PROVIDER_IDS,
) -> MigrationReport:
    """Move household-scoped computer data to the personal root.

    Safe to call on every boot; only rows still scoped to the household id
    are candidates, so a completed migration is a no-op.
    """

    household_id = identity.current_principal().household_id
    personal_id = identity.personal_scope_id
    migrated_resources: list[tuple[str, str, str]] = []
    migrated_claims: list[tuple[str, str, str]] = []

    moved_resource_ids: set[str] = set()
    for record in resources.list_all():
        if record.scope_id != household_id:
            continue
        if record.provider_id not in computer_provider_ids:
            continue  # home-provider and other objects stay home-specific
        resources.save(replace(record, scope_id=personal_id))
        migrated_resources.append((record.resource_id, household_id, personal_id))
        moved_resource_ids.add(record.resource_id)

    for claim in claims.list_all():
        if claim.scope_id != household_id:
            continue
        if not _claim_is_personal(claim, moved_resource_ids, resources, household_id):
            continue  # built on home resources, or explicitly home-specific
        claims.save(replace(claim, scope_id=personal_id))
        migrated_claims.append((claim.claim_id, household_id, personal_id))

    note = None
    if migrated_resources or migrated_claims:
        note = (
            f"Household-first migration: {len(migrated_resources)} computer resource(s) and "
            f"{len(migrated_claims)} claim(s) moved from the legacy household scope to the "
            "personal root; the household scope is retained as a legacy child scope. "
            "Historical receipts naming the household id for these objects read as the "
            "personal root."
        )
        scope_store.record_legacy_mapping(
            legacy_scope_id=household_id,
            canonical_scope_id=personal_id,
            note=note,
            at=clock(),
        )
    return MigrationReport(
        household_scope_id=household_id,
        personal_scope_id=personal_id,
        migrated_resources=tuple(migrated_resources),
        migrated_claims=tuple(migrated_claims),
        legacy_mapping_note=note,
    )


def _claim_is_personal(claim, moved_resource_ids: set[str], resources, household_id: str) -> bool:
    """A claim migrates when it is built on moved computer resources, or when
    it is a pure user statement (no resource or device backing). A claim
    anchored to home evidence -- device refs, or refs resolving to resources
    that stayed in the household scope -- is home-specific and stays."""

    refs = [str(ref) for ref in claim.source_refs]
    if any(ref in moved_resource_ids for ref in refs):
        return True
    if not refs:
        return True
    for ref in refs:
        if ref.startswith("device:"):
            return False
        record = resources.get(ref)
        if record is not None and record.scope_id == household_id:
            # A resource that stayed home-specific: home evidence wins over
            # any user-statement default.
            return False
    return True


def rollback_migration(
    report: MigrationReport,
    *,
    scope_store: ScopeStore,
    resources: ResourceStore,
    claims: ClaimStore,
) -> None:
    """Reverse one migration exactly, from its report."""

    for resource_id, old_scope, _new_scope in report.migrated_resources:
        record = resources.get(resource_id)
        if record is not None:
            resources.save(replace(record, scope_id=old_scope))
    for claim_id, old_scope, _new_scope in report.migrated_claims:
        claim = claims.get(claim_id)
        if claim is not None:
            claims.save(replace(claim, scope_id=old_scope))
    if report.legacy_mapping_note is not None:
        # The mapping row is itself the audit record of the migration; the
        # rollback removes it so the store reflects the restored layout.
        scope_store.remove_legacy_mapping(report.household_scope_id)


__all__ = [
    "COMPUTER_PROVIDER_IDS",
    "HOME_PROVIDER_IDS",
    "MigrationReport",
    "migrate_household_first_installation",
    "rollback_migration",
]
