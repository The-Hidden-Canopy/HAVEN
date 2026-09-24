"""Governed window focus: the single write-side window capability.

Spec page 30: bringing a window to the foreground is low-risk but still
crosses authority -- every request is decided by `ResourceAuthorityEngine`,
executed by the observation provider, consequence-verified (the target
must actually be foreground afterwards), and receipted in the action
ledger. There is deliberately no close/launch/type: "seen" never implies
control.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from haven.actions import (
    ActionLedgerEntry,
    ActionLedgerStore,
    ResourceActionDecision,
    ResourceActionRequest,
    ResourceAuthorityEngine,
)
from haven.core.domain import DecisionStatus, RiskTier
from haven.integrations.computer.windows import PROVIDER_ID, WindowObservationProvider
from haven.resources.store import ResourceStore

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class WindowActionService:
    """Request -> authority decision -> provider execution -> receipt."""

    def __init__(
        self,
        *,
        director,
        provider: WindowObservationProvider,
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
            action_risk={"window.focus": RiskTier.SAFE_AUTOMATIC}
        )

    def set_director(self, director) -> None:
        self._director = director

    def set_provider(self, provider: WindowObservationProvider) -> None:
        self._provider = provider

    def set_resource_store(self, resource_store: ResourceStore) -> None:
        self._resource_store = resource_store

    def set_ledger(self, ledger: ActionLedgerStore) -> None:
        self._ledger = ledger

    def request_focus(
        self,
        *,
        resource_id: str | None,
        justification: str | None = None,
    ) -> dict:
        if not self._director.has_declared_owner:
            return {"ok": False, "error": "no household owner declared yet"}
        if not isinstance(resource_id, str) or not resource_id.strip():
            return {"ok": False, "error": "a non-empty 'resource_id' is required"}
        record = self._resource_store.get(resource_id.strip())
        if record is None or record.stale:
            return {"ok": False, "error": "the named window is unknown or no longer current"}
        if record.provider_id != PROVIDER_ID:
            return {"ok": False, "error": "the named resource is not a live window"}
        if "window.focus" not in record.capabilities:
            return {"ok": False, "error": "the named window does not declare the 'window.focus' capability"}

        now = self._clock()
        principal = self._director.resident
        request = ResourceActionRequest(
            request_id=_new_id("action"),
            household_id=principal.household_id,
            requested_by=principal.actor_id,
            provider_id=PROVIDER_ID,
            action="window.focus",
            resource_id=record.resource_id,
            parameters=(("hwnd", _hwnd_of(record.metadata)),),
            justification=(
                justification.strip()
                if isinstance(justification, str) and justification.strip()
                else "owner brought a window to the foreground from HAVEN"
            ),
            requested_at=now,
        )
        decision = self._engine.decide(request, principal=principal, now=now)
        if decision.status != DecisionStatus.ALLOW:
            self._record(request, decision, success=None, detail=None)
            return {"ok": False, "error": decision.reason}

        hwnd = int(dict(request.parameters)["hwnd"])
        success = self._provider.bring_window_to_foreground(hwnd)
        detail = None
        if success:
            # Consequence verification: the target must actually be in the
            # foreground afterwards, and the projection refreshed.
            self._provider.observe_and_project()
            foreground = self._provider.enumerate_windows()
            focused = next((item for item in foreground if item.hwnd == hwnd), None)
            success = focused is not None and self._provider_is_foreground(hwnd)
            detail = "verified in foreground" if success else "focus was refused by the OS"
        self._record(request, decision, success=success, detail=detail)
        return {"ok": True, "success": success, "detail": detail}

    def _provider_is_foreground(self, hwnd: int) -> bool:
        try:
            return self._provider._foreground_hwnd() == hwnd  # noqa: SLF001 -- seam shared with observation
        except Exception:
            return False

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


def _hwnd_of(metadata) -> str:
    for key, value in metadata:
        if key == "hwnd":
            return str(value)
    raise ValueError("window resource is missing its hwnd metadata")


__all__ = ["WindowActionService"]
