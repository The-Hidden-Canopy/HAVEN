"""Governed browser tab actions (spec page 31).

Tiering per the spec: focus tab is low-risk; opening a URL is a direct
command; closing a tab is confirmation-gated because unsaved state in the
tab cannot be ruled out. Fill/click/submit are deliberately absent -- they
are a future high-authority provider, not this one. Commands reach the
browser only through a connected connector; with none, capability states
are explicit refusals, never silent no-ops.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from haven.actions import (
    ActionLedgerEntry,
    ActionLedgerStore,
    ResourceActionDecision,
    ResourceActionRequest,
    ResourceAuthorityEngine,
)
from haven.core.domain import ConfirmationToken, DecisionStatus, RiskTier
from haven.integrations.browser import (
    PROVIDER_ID,
    BrowserObservationProvider,
    tab_resource_id,
)
from haven.resources.store import ResourceStore

_CONFIRMATION_WINDOW = timedelta(minutes=5)

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class BrowserActionService:
    """Focus/open execute directly (receipted); close pends a confirmation."""

    def __init__(
        self,
        *,
        director,
        provider: BrowserObservationProvider,
        resource_store: ResourceStore,
        ledger: ActionLedgerStore,
        clock=_DEFAULT_CLOCK,
        engine: ResourceAuthorityEngine | None = None,
    ) -> None:
        self._director = director
        self._provider = provider
        self._resource_store = resource_store
        self._ledger = ledger
        self._clock = clock
        self._engine = engine or ResourceAuthorityEngine(
            action_risk={
                "browser.tab.focus": RiskTier.SAFE_AUTOMATIC,
                "browser.tab.open": RiskTier.SAFE_AUTOMATIC,
                "browser.tab.close": RiskTier.CONFIRMATION_REQUIRED,
            }
        )
        self._pending: dict[str, ResourceActionRequest] = {}

    def set_director(self, director) -> None:
        self._director = director

    def set_provider(self, provider: BrowserObservationProvider) -> None:
        self._provider = provider

    def set_resource_store(self, resource_store: ResourceStore) -> None:
        self._resource_store = resource_store

    def set_ledger(self, ledger: ActionLedgerStore) -> None:
        self._ledger = ledger

    # -- entry points ---------------------------------------------------------

    def focus_tab(self, *, resource_id: str | None) -> dict:
        record = self._live_tab_record(resource_id)
        if isinstance(record, dict):
            return record
        return self._execute(
            action="browser.tab.focus",
            record=record,
            parameters={"tab_id": _tab_id_of(record.metadata)},
        )

    def open_url(self, *, browser: str | None, url: str | None) -> dict:
        if not self._director.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not isinstance(browser, str) or not browser.strip():
            return {"ok": False, "error": "a non-empty 'browser' is required"}
        if not isinstance(url, str) or not url.strip():
            return {"ok": False, "error": "a non-empty 'url' is required"}
        return self._execute(
            action="browser.tab.open",
            record=None,
            parameters={"browser": browser.strip(), "url": url.strip()},
        )

    def close_tab(self, *, resource_id: str | None) -> dict:
        record = self._live_tab_record(resource_id)
        if isinstance(record, dict):
            return record
        return self._execute(
            action="browser.tab.close",
            record=record,
            parameters={"tab_id": _tab_id_of(record.metadata)},
        )

    def confirm_close(self, *, request_id: str | None) -> dict:
        return self._confirm(request_id)

    def deny_close(self, *, request_id: str | None) -> dict:
        if not isinstance(request_id, str) or not request_id.strip():
            return {"ok": False, "error": "a non-empty 'request_id' is required"}
        request = self._pending.pop(request_id.strip(), None)
        if request is None:
            return {"ok": False, "error": "no pending action with that request_id"}
        self._record(
            request,
            ResourceActionDecision(DecisionStatus.DENY, "denied by household member before confirming"),
            success=None,
            detail=None,
        )
        return {"ok": True}

    # -- shared machinery -------------------------------------------------------

    def _live_tab_record(self, resource_id):
        if not self._director.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not isinstance(resource_id, str) or not resource_id.strip():
            return {"ok": False, "error": "a non-empty 'resource_id' is required"}
        record = self._resource_store.get(resource_id.strip())
        if record is None:
            # The tab may exist in the provider before anything projected it:
            # fold the current set once, then answer from the durable record.
            self._provider.observe_and_project()
            record = self._resource_store.get(resource_id.strip())
        if record is None or record.stale or record.provider_id != PROVIDER_ID:
            return {"ok": False, "error": "the named tab is unknown, closed, or not a browser tab"}
        if record.resource_type != "browser_tab":
            return {"ok": False, "error": "the named resource is not a browser tab"}
        return record

    def _execute(self, *, action: str, record, parameters: dict) -> dict:
        now = self._clock()
        principal = self._director.resident
        request = ResourceActionRequest(
            request_id=_new_id("action"),
            household_id=principal.household_id,
            requested_by=principal.actor_id,
            provider_id=PROVIDER_ID,
            action=action,
            resource_id=record.resource_id if record is not None else None,
            parameters=tuple(parameters.items()),
            justification="owner acted on a browser tab from HAVEN",
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
        return self._dispatch(request, decision)

    def _confirm(self, request_id: str | None) -> dict:
        if not isinstance(request_id, str) or not request_id.strip():
            return {"ok": False, "error": "a non-empty 'request_id' is required"}
        request = self._pending.get(request_id.strip())
        if request is None:
            return {"ok": False, "error": "no pending action with that request_id"}
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
        confirmed = ResourceActionRequest(
            request_id=request.request_id,
            household_id=request.household_id,
            requested_by=request.requested_by,
            provider_id=request.provider_id,
            action=request.action,
            resource_id=request.resource_id,
            parameters=request.parameters,
            justification=request.justification,
            requested_at=request.requested_at,
            confirmation_token=token,
        )
        decision = self._engine.decide(confirmed, principal=principal, now=now)
        del self._pending[confirmed.request_id]
        if decision.status != DecisionStatus.ALLOW:
            self._record(confirmed, decision, success=None, detail=None)
            return {"ok": False, "error": decision.reason}
        return self._dispatch(confirmed, decision)

    def _dispatch(self, request: ResourceActionRequest, decision) -> dict:
        parameters = dict(request.parameters)
        if request.action == "browser.tab.focus":
            tab = self._provider.get_tab(str(parameters["tab_id"]))
            if tab is None:
                return {"ok": False, "error": "the tab is no longer open"}
            result = self._provider._hub.send_command(  # noqa: SLF001 -- hub is the connector seam
                tab.browser, "focus_tab", tab_id=tab.tab_id
            )
        elif request.action == "browser.tab.open":
            result = self._provider._hub.send_command(  # noqa: SLF001
                str(parameters["browser"]), "open_url", url=str(parameters["url"])
            )
        else:  # browser.tab.close, only reachable after confirmation
            tab = self._provider.get_tab(str(parameters["tab_id"]))
            if tab is None:
                return {"ok": False, "error": "the tab is already closed"}
            result = self._provider._hub.send_command(  # noqa: SLF001
                tab.browser, "close_tab", tab_id=tab.tab_id
            )
            if result.accepted:
                self._provider.mark_tab_closed(tab.tab_id)
                self._provider.observe_and_project()

        self._record(request, decision, success=result.accepted, detail=result.detail)
        return {"ok": True, "success": result.accepted, "detail": result.detail}

    def _record(self, request, decision, *, success: bool | None, detail: str | None) -> None:
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


def _tab_id_of(metadata) -> str:
    for key, value in metadata:
        if key == "tab_id":
            return str(value)
    raise ValueError("browser tab resource is missing its tab_id metadata")


__all__ = ["BrowserActionService"]
