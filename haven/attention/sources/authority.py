"""AuthoritySource: pending governed decisions (spec 6, "AuthoritySource").

Reads the two existing pending-confirmation pools -- `HavenApplication`'s
device/rule confirmations (which also carry external-agent-originated
requests via `PendingRequest.external_connection_id`) and
`ComputerActionService`'s filesystem-mutation confirmations -- without
introducing a third. Authority items are never dismissible or snoozable
(spec 5.1): hiding a pending decision would create an invisible unresolved
permission request.
"""

from __future__ import annotations

from datetime import datetime

from ..domain import AttentionActionRef, AttentionItem, AttentionKind, AttentionSeverity, Dismissibility, RouteRef

_ACTION_LABEL = {
    "filesystem.move": "This move",
    "filesystem.copy": "This copy",
    "filesystem.rename": "This rename",
    "filesystem.create_folder": "This folder creation",
}


class AuthoritySource:
    def __init__(self, *, director, computer_actions, identity) -> None:
        self._director = director
        self._computer_actions = computer_actions
        self._identity = identity

    def collect(self, *, now: datetime, visible_scope_ids: tuple[str, ...] | None = None) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        items.extend(self._device_and_external_pending(now))
        items.extend(self._computer_pending())
        return items

    def _device_and_external_pending(self, now: datetime) -> list[AttentionItem]:
        try:
            pending = self._director.pending_requests
        except Exception:
            pending = ()
        items = []
        for request in pending:
            external = request.external_connection_id is not None
            source_domain = "external_agents" if external else "home"
            title = request.title or "A decision is waiting"
            why_now = request.detail or "Your approval is required before anything changes."
            items.append(
                AttentionItem(
                    attention_id=f"authority:pending:{request.request_id}",
                    kind=AttentionKind.AUTHORITY,
                    severity=AttentionSeverity.HIGH,
                    title=title,
                    summary=why_now,
                    why_now=why_now,
                    source_domain=source_domain,
                    source_ref=f"pending:{request.request_id}",
                    scope_id=self._identity.personal_scope_id,
                    created_at=now,
                    expires_at=request.expires_at,
                    evidence_refs=(request.request_id,),
                    route=RouteRef(page="home", action_hint="review", entity_id=request.request_id),
                    available_actions=(AttentionActionRef(action="review", label="Review"),),
                    dismissibility=Dismissibility.NONE,
                )
            )
        return items

    def _computer_pending(self) -> list[AttentionItem]:
        if self._computer_actions is None:
            return []
        try:
            pending = self._computer_actions.list_pending()
        except Exception:
            pending = ()
        items = []
        for request in pending:
            label = _ACTION_LABEL.get(request.action, f"'{request.action}'")
            why_now = f"{label} requires confirmation before HAVEN can execute it."
            items.append(
                AttentionItem(
                    attention_id=f"authority:computer:{request.request_id}",
                    kind=AttentionKind.AUTHORITY,
                    severity=AttentionSeverity.HIGH,
                    title=f"{label} is waiting for approval",
                    summary=why_now,
                    why_now=why_now,
                    source_domain="computer",
                    source_ref=f"computer-request:{request.request_id}",
                    scope_id=request.household_id,
                    created_at=request.requested_at,
                    evidence_refs=(request.request_id,),
                    route=RouteRef(page="computer", action_hint="review", entity_id=request.request_id),
                    available_actions=(AttentionActionRef(action="review", label="Review"),),
                    dismissibility=Dismissibility.NONE,
                )
            )
        return items


__all__ = ["AuthoritySource"]
