"""`LocalIdentityProvider`: the private, local identity behind `IdentityProvider`.

First run mints a stable `principal_id` and `personal_scope_id` and
persists them in `<data_dir>/identity.json`; every later boot reads the
same pair back, so scopes, memberships and anything keyed to the principal
survive restarts (design spec pages 18-19). A mint is all-or-nothing:
partial files are rejected, never interpreted.

The provider answers the two contract questions -- who is acting, and
which scopes they belong to -- from *stored memberships only*. Visibility
is never widened by caller input; the authority boundary intersects
caller-supplied scope lists with `memberships()` and denies the rest.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from haven.core.domain import Principal, RoleTier
from haven.scopes.models import Membership, ScopeRef
from haven.scopes.store import ScopeStore

from .contracts import ScopeMembership

_IDENTITY_FILENAME = "identity.json"


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class LocalIdentityProvider:
    """File-backed local identity over one `ScopeStore`'s memberships."""

    def __init__(
        self,
        *,
        path: str | Path,
        household_id: str,
        scope_store: ScopeStore,
        clock=_default_clock,
    ) -> None:
        self._path = Path(path)
        self._household_id = _require_text(household_id, name="household_id")
        self._scopes = scope_store
        self._clock = clock
        principal_id, personal_scope_id = self._load_or_mint()
        self._principal_id = principal_id
        self._personal_scope_id = personal_scope_id

    @property
    def principal_id(self) -> str:
        return self._principal_id

    @property
    def personal_scope_id(self) -> str:
        return self._personal_scope_id

    @property
    def path(self) -> Path:
        return self._path

    def _load_or_mint(self) -> tuple[str, str]:
        """Read the persisted identity, or mint and durably write a new one."""

        if self._path.is_file():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            principal_id = _require_text(data.get("principal_id"), name="principal_id")
            personal_scope_id = _require_text(data.get("personal_scope_id"), name="personal_scope_id")
            return principal_id, personal_scope_id
        principal_id = f"principal:{uuid4()}"
        personal_scope_id = f"scope:{uuid4()}"
        self._write({"principal_id": principal_id, "personal_scope_id": personal_scope_id})
        return principal_id, personal_scope_id

    def _write(self, payload: dict) -> None:
        """Write via temp-file + os.replace so a crash cannot halve the file."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
            os.replace(temp_name, self._path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    # -- IdentityProvider ------------------------------------------------------

    def current_principal(self) -> Principal:
        """The acting principal.

        `Principal` is the governed home loop's actor type (`actor_id` +
        `household_id` + `role_tier`) and stays untouched; the principal id
        rides `actor_id` so receipts name the same principal the scope layer
        knows. The household id remains the loop's scope; the wider scope
        question is `memberships()`'s job, not this field's.
        """

        return Principal(
            actor_id=self._principal_id,
            household_id=self._household_id,
            role_tier=RoleTier.OWNER,
        )

    def memberships(self, *, now: datetime | None = None) -> tuple[ScopeMembership, ...]:
        at = now or self._clock()
        result: list[ScopeMembership] = []
        for membership in self._scopes.memberships_for(self._principal_id, now=at):
            scope = self._scopes.get_scope(membership.scope_id)
            if scope is None or scope.status != "active":
                continue
            result.append(
                ScopeMembership(scope=scope, actor_id=membership.principal_id, role=membership.role)
            )
        return tuple(result)

    def visible_scope_ids(self, *, now: datetime | None = None) -> tuple[str, ...]:
        return self._scopes.visible_scope_ids(self._principal_id, now=now)


def provision_identity(
    *,
    data_dir: str | Path,
    household_id: str,
    scope_store: ScopeStore,
    clock=_default_clock,
    household_name: str = "Household",
) -> tuple[LocalIdentityProvider, bool]:
    """First-run provisioning of the personal-root scope layout.

    Creates (idempotently):
      * the personal root scope (`kind="personal"`, no parent),
      * the household as one child scope beneath it, keeping its existing
        id so every receipt written under it stays valid,
      * memberships: the principal owns the personal root and is a member
        of the household scope.

    Returns the provider plus whether this call created the personal root
    (True on first run / a household-first migration, False on later
    boots); that flag is the migration trigger.
    """

    data_dir = Path(data_dir)
    path = data_dir / _IDENTITY_FILENAME
    first_run = not path.is_file()
    provider = LocalIdentityProvider(
        path=path, household_id=household_id, scope_store=scope_store, clock=clock
    )
    now = clock()
    if first_run:
        scope_store.save_scope(
            ScopeRef(
                scope_id=provider.personal_scope_id,
                kind="personal",
                name="Personal",
                created_at=now,
            )
        )
        scope_store.save_scope(
            ScopeRef(
                scope_id=household_id,
                kind="household",
                name=household_name,
                parent_scope_id=provider.personal_scope_id,
                created_at=now,
            )
        )

        scope_store.add_membership(
            Membership(
                principal_id=provider.principal_id,
                scope_id=provider.personal_scope_id,
                role="owner",
                capabilities=("administer", "write"),
                valid_from=now,
            )
        )
        scope_store.add_membership(
            Membership(
                principal_id=provider.principal_id,
                scope_id=household_id,
                role="member",
                capabilities=("write",),
                valid_from=now,
            )
        )
    return provider, first_run


__all__ = ["LocalIdentityProvider", "provision_identity"]
