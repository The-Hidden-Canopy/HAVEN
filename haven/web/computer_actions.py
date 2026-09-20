"""`ComputerActionService`: authorization and consequence verification for
`FilesystemProvider`'s governed resource actions.

This is the seam an earlier review named directly: "computer mutation is
not yet wired through the actual HAVEN authority/runtime path" -- real, but
unreachable from anywhere. This module makes it reachable, deliberately not
by routing filesystem actions through `haven.authority.policy.AuthorityEngine`
(built for the closed device/`ActionKind` vertical -- see
`haven.actions.models` for why) but through the parallel, provider-agnostic
`haven.actions.ResourceAuthorityEngine`, reusing the same `Principal`/
`RoleTier`/`ConfirmationToken` primitives and the same
"no household owner declared yet" gate `HavenApplication.device_command`
already enforces for devices.

Every requested action:

1. is decided by `ResourceAuthorityEngine` against the household's own
   resident `Principal` -- household scope, MEMBER-or-higher role, a
   non-empty justification, and the action's risk tier
   (`FILESYSTEM_ACTION_RISK`);
2. when a `resource_id` is named, is cross-checked against the *current*
   `ResourceStore` record for it -- unknown, stale, a declared capability
   that does not list this action, or a `source` parameter that does not
   match that resource's own `locator` are all denied here, the same
   "the device manifest is authoritative, not the caller's claim" discipline
   `haven.authority.policy.risk_for_request` already applies to devices;
3. if CONFIRMATION_REQUIRED, is held server-side (`_pending`) until
   `confirm_action()` supplies a fresh, single-use token -- in-memory only;
   a lost pending confirmation on restart is an honest boundary the same
   in-memory `HavenApplication._pending` dict already accepts, not a
   promise a durable row for a still-undecided attempt would be right to
   make;
4. once ALLOWed, is executed by the real `FilesystemProvider` through its
   provider-neutral `ProviderCommand` seam, which re-validates every path
   itself regardless of this layer's own checks --
   defense in depth, matching every other integration in this repo;
5. has its consequence verified: whatever it touched is re-observed right
   away and folded into the `ResourceStore` (`_verify_consequence`) -- a
   search run immediately after a move does not have to wait for the next
   `scan_computer_provider()` to learn the file relocated;
6. is recorded into `ActionLedgerStore` regardless of outcome -- denied,
   still pending confirmation, or executed -- so a household can see every
   attempt HAVEN made against its files, not only the ones that worked.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from ..actions import ActionLedgerEntry, ActionLedgerStore, ResourceActionDecision, ResourceActionRequest, ResourceAuthorityEngine
from ..core.domain import ConfirmationToken, DecisionStatus
from ..execution import ProviderCommand
from ..integrations.computer.filesystem import FILESYSTEM_ACTION_RISK, FilesystemProvider
from ..resources import ResourceStore
from .computer_provider import build_filesystem_provider, load_computer_provider_config
from .setup_config import SetupConfigStore

_CONFIRMATION_WINDOW = timedelta(minutes=5)

# Actions whose first parameter names the resource being acted on -- used
# to cross-check a caller-supplied `resource_id` against that resource's
# own current `locator` rather than trusting the two were named
# consistently.
_SOURCE_PARAMETER = {
    "filesystem.copy": "source",
    "filesystem.move": "source",
    "filesystem.rename": "source",
    "filesystem.open": "path",
    "filesystem.reveal": "path",
}

_RESOURCE_REQUIRED = frozenset({"filesystem.open", "filesystem.reveal"})


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class ComputerActionService:
    def __init__(
        self,
        *,
        store: SetupConfigStore,
        director,
        resource_store: ResourceStore,
        ledger: ActionLedgerStore,
        clock=None,
        open_path: Callable[[Path], None] | None = None,
        reveal_path: Callable[[Path], None] | None = None,
    ) -> None:
        self._store = store
        self._director = director
        self._resource_store = resource_store
        self._ledger = ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._open_path = open_path
        self._reveal_path = reveal_path
        self._engine = ResourceAuthorityEngine(action_risk=FILESYSTEM_ACTION_RISK)
        self._pending: dict[str, ResourceActionRequest] = {}

    def set_director(self, director) -> None:
        """Rebind to a freshly rebuilt director without discarding
        `_pending` -- `rebuild_director()` runs on every setup change, most
        of which (declaring a person, installing an unrelated provider)
        have nothing to do with computer access; a household member mid-way
        through confirming a move should not lose that confirmation because
        someone else's tab renamed a room."""

        self._director = director

    def set_resource_store(self, resource_store: ResourceStore) -> None:
        self._resource_store = resource_store

    def set_ledger(self, ledger: ActionLedgerStore) -> None:
        self._ledger = ledger

    def _provider(self) -> FilesystemProvider | None:
        config = load_computer_provider_config(self._store)
        return build_filesystem_provider(
            config,
            scope_id=self._director.household_id,
            open_path=self._open_path,
            reveal_path=self._reveal_path,
        )

    # -- the three caller-facing entry points ------------------------------

    def request_action(
        self,
        *,
        action: str | None,
        resource_id: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        justification: str | None = None,
    ) -> dict:
        if not self._director.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not isinstance(action, str) or not action.strip():
            return {"ok": False, "error": "a non-empty 'action' is required"}
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "a non-empty 'justification' is required"}
        provider = self._provider()
        if provider is None:
            return {"ok": False, "error": "computer access is not enabled, or no allowed folder is reachable"}

        resource_error = self._check_named_resource(action=action, resource_id=resource_id, parameters=parameters)
        if resource_error is not None:
            return {"ok": False, "error": resource_error}

        now = self._clock()
        principal = self._director.resident
        request = ResourceActionRequest(
            request_id=_new_id("action"),
            household_id=principal.household_id,
            requested_by=principal.actor_id,
            provider_id=provider.provider_id,
            action=action,
            resource_id=resource_id,
            parameters=tuple((parameters or {}).items()),
            justification=justification,
            requested_at=now,
        )
        decision = self._engine.decide(request, principal=principal, now=now)

        if decision.status == DecisionStatus.CONFIRMATION_REQUIRED:
            self._pending[request.request_id] = request
            self._record(request, decision, success=None, detail=None)
            return {
                "ok": True,
                "status": "confirmation_required",
                "request_id": request.request_id,
                "reason": decision.reason,
            }
        if decision.status != DecisionStatus.ALLOW:
            self._record(request, decision, success=None, detail=None)
            return {"ok": False, "error": decision.reason}

        return self._execute(provider, request, decision)

    def confirm_action(self, *, request_id: str | None) -> dict:
        if not isinstance(request_id, str) or not request_id.strip():
            return {"ok": False, "error": "a non-empty 'request_id' is required"}
        request = self._pending.get(request_id)
        if request is None:
            return {"ok": False, "error": "no pending action with that request_id"}
        provider = self._provider()
        if provider is None:
            del self._pending[request_id]
            return {"ok": False, "error": "computer access is not enabled, or no allowed folder is reachable"}

        now = self._clock()
        principal = self._director.resident
        token = ConfirmationToken(
            token_id=_new_id("confirm"),
            household_id=request.household_id,
            rule_id=request.request_id,
            request_id=request.request_id,
            confirmed_by=principal.actor_id,
            issued_at=now,
            expires_at=now + _CONFIRMATION_WINDOW,
        )
        confirmed = replace(request, confirmation_token=token)
        decision = self._engine.decide(confirmed, principal=principal, now=now)
        del self._pending[request_id]
        if decision.status != DecisionStatus.ALLOW:
            self._record(confirmed, decision, success=None, detail=None)
            return {"ok": False, "error": decision.reason}

        return self._execute(provider, confirmed, decision)

    def deny_action(self, *, request_id: str | None) -> dict:
        if not isinstance(request_id, str) or not request_id.strip():
            return {"ok": False, "error": "a non-empty 'request_id' is required"}
        request = self._pending.pop(request_id, None)
        if request is None:
            return {"ok": False, "error": "no pending action with that request_id"}
        self._record(
            request,
            ResourceActionDecision(DecisionStatus.DENY, "denied by household member before confirming"),
            success=None,
            detail=None,
        )
        return {"ok": True}

    def history(self, *, limit: int = 50) -> dict:
        entries = self._ledger.list_by_household(self._director.household_id, limit=limit)
        return {
            "ok": True,
            "entries": [
                {
                    "entry_id": entry.entry_id,
                    "provider_id": entry.provider_id,
                    "action": entry.action,
                    "resource_id": entry.resource_id,
                    "requested_by": entry.requested_by,
                    "justification": entry.justification,
                    "status": entry.status.value,
                    "reason": entry.reason,
                    "recorded_at": entry.recorded_at.isoformat(),
                    "success": entry.success,
                    "detail": entry.detail,
                }
                for entry in entries
            ],
        }

    # -- resource-authoritative cross-check --------------------------------

    def _check_named_resource(
        self, *, action: str, resource_id: str | None, parameters: Mapping[str, Any] | None
    ) -> str | None:
        """Refuse a request naming a `resource_id` that current reality does
        not back up. `None` (no `resource_id` given) is not an error --
        `filesystem.create_folder` has nothing to name yet."""

        if action in _RESOURCE_REQUIRED and resource_id is None:
            return "a current resource_id is required for this action"
        if resource_id is None:
            return None
        record = self._resource_store.get(resource_id)
        if record is None or record.stale:
            return "the named resource is unknown or no longer current"
        if action not in record.capabilities:
            return f"the named resource does not declare the {action!r} capability"
        source_parameter = _SOURCE_PARAMETER.get(action)
        if source_parameter is not None:
            source = (parameters or {}).get(source_parameter)
            if source is not None and str(Path(source)) != str(Path(record.locator)):
                return "the named resource does not match the action's own source path"
        return None

    # -- execution + consequence verification ------------------------------

    def _execute(self, provider: FilesystemProvider, request: ResourceActionRequest, decision) -> dict:
        now = self._clock()
        parameters = dict(request.parameters)
        if request.action in _RESOURCE_REQUIRED and "path" not in parameters and request.resource_id:
            record = self._resource_store.get(request.resource_id)
            if record is not None and record.locator is not None:
                parameters["path"] = record.locator
        command = ProviderCommand(
            request_id=request.request_id,
            provider_id=request.provider_id,
            capability=request.action,
            target_resource_id=request.resource_id,
            parameters=tuple(parameters.items()),
            requested_at=now,
        )
        result = provider.execute_provider(command)
        if result.success:
            self._verify_consequence(provider, request)
        self._record(request, decision, success=result.success, detail=result.detail)
        return {"ok": True, "success": result.success, "detail": result.detail}

    def _verify_consequence(self, provider: FilesystemProvider, request: ResourceActionRequest) -> None:
        """Fold what actually happened back into the `ResourceStore` right
        away, rather than leaving it to the next full `scan_computer_provider()`
        to notice. `provider.execute()` already verified the OS-level claim
        (see its own docstring); this is the separate claim that HAVEN's
        durable record of the filesystem is now true again too."""

        params = dict(request.parameters)
        if request.action == "filesystem.create_folder":
            self._observe_and_save(provider, params.get("path"))
        elif request.action == "filesystem.copy":
            self._observe_and_save(provider, params.get("destination"))
        elif request.action == "filesystem.move":
            self._stale_moved_source(provider, params.get("source"))
            self._observe_and_save(provider, params.get("destination"))
        elif request.action == "filesystem.rename":
            source = params.get("source")
            new_name = params.get("new_name")
            self._stale_moved_source(provider, source)
            if source and new_name:
                self._observe_and_save(provider, str(Path(source).with_name(new_name)))

    def _observe_and_save(self, provider: FilesystemProvider, path: str | None) -> None:
        if not path:
            return
        record = provider.observe_one(path)
        if record is not None:
            self._resource_store.save(record)

    def _stale_moved_source(self, provider: FilesystemProvider, path: str | None) -> None:
        if not path:
            return
        self._resource_store.mark_stale(provider.resource_id_for(path))

    # -- the ledger ----------------------------------------------------------

    def _record(
        self,
        request: ResourceActionRequest,
        decision: ResourceActionDecision,
        *,
        success: bool | None,
        detail: str | None,
    ) -> None:
        self._ledger.save(
            ActionLedgerEntry(
                entry_id=_new_id("ledger"),
                household_id=request.household_id,
                provider_id=request.provider_id,
                action=request.action,
                resource_id=request.resource_id,
                requested_by=request.requested_by,
                justification=request.justification,
                parameters=request.parameters,
                status=decision.status,
                reason=decision.reason,
                recorded_at=self._clock(),
                success=success,
                detail=detail,
            )
        )


__all__ = ["ComputerActionService"]
