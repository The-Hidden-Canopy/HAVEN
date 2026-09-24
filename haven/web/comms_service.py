"""Comms service: config, projection, governed calendar mutations, proposals.

External sources stay authoritative: a governed calendar write is executed
by the adapter and then verified by re-reading the same source. Events can
attach to projects (relationship assertions) and propose tasks -- PROPOSED,
never auto-created (spec page 32).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from haven.actions import (
    ActionLedgerEntry,
    ActionLedgerStore,
    ResourceActionDecision,
    ResourceActionRequest,
    ResourceAuthorityEngine,
)
from haven.core.domain import ConfirmationToken, DecisionStatus, RiskTier
from haven.integrations.comms import (
    EMAIL_PROVIDER_ID,
    ICS_PROVIDER_ID,
    CalendarEvent,
    LocalIcsCalendarProvider,
    LocalMaildirProvider,
    UnconfiguredEmailProvider,
)
from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

_CONFIRMATION_WINDOW = timedelta(minutes=5)

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def event_resource_id(event_id: str) -> str:
    return f"calevent:{event_id}"


class CommsService:
    """One façade over the comms providers: reads project, writes are governed."""

    def __init__(
        self,
        *,
        config_path: str | Path,
        resource_store: ResourceStore,
        ledger: ActionLedgerStore,
        tasks_service,
        identity,
        scope_id: str,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        self._config_path = Path(config_path)
        self._resources = resource_store
        self._ledger = ledger
        self._tasks = tasks_service
        self._identity = identity
        self._scope_id = scope_id
        self._clock = clock
        self._lock = threading.Lock()
        self._engine = ResourceAuthorityEngine(
            action_risk={
                "calendar.event.create": RiskTier.CONFIRMATION_REQUIRED,
                "calendar.event.update": RiskTier.CONFIRMATION_REQUIRED,
                "calendar.event.delete": RiskTier.CONFIRMATION_REQUIRED,
            }
        )
        self._pending: dict[str, ResourceActionRequest] = {}
        self._config = self._load_config()

    # -- configuration ----------------------------------------------------------

    def _load_config(self) -> dict:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"calendar_sources": [], "maildir": None}
        if not isinstance(data, dict):
            return {"calendar_sources": [], "maildir": None}
        return {
            "calendar_sources": [
                str(item) for item in data.get("calendar_sources", []) if isinstance(item, str)
            ],
            "maildir": data.get("maildir") if isinstance(data.get("maildir"), str) else None,
        }

    def _save_config(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(self._config, indent=2), encoding="utf-8")

    def add_calendar_source(self, path: str) -> dict:
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "a non-empty 'path' is required"}
        with self._lock:
            if path.strip() not in self._config["calendar_sources"]:
                self._config["calendar_sources"].append(path.strip())
                self._save_config()
        return {"ok": True, "calendar_sources": list(self._config["calendar_sources"])}

    def remove_calendar_source(self, path: str) -> dict:
        with self._lock:
            if path in self._config["calendar_sources"]:
                self._config["calendar_sources"] = [
                    item for item in self._config["calendar_sources"] if item != path
                ]
                self._save_config()
        return {"ok": True, "calendar_sources": list(self._config["calendar_sources"])}

    def set_maildir(self, path: str | None) -> dict:
        with self._lock:
            self._config["maildir"] = path.strip() if isinstance(path, str) and path.strip() else None
            self._save_config()
        return {"ok": True, "maildir": self._config["maildir"]}

    # -- provider seams ------------------------------------------------------------

    def calendar_provider(self) -> LocalIcsCalendarProvider:
        return LocalIcsCalendarProvider(self._config["calendar_sources"])

    def email_provider(self):
        if not self._config["maildir"]:
            return UnconfiguredEmailProvider()
        return LocalMaildirProvider(self._config["maildir"])

    # -- reads + projection -----------------------------------------------------------

    def list_events(self) -> dict:
        provider = self.calendar_provider()
        events = provider.events()
        now = self._clock()
        for event in events:
            self._resources.save(
                ResourceRecord(
                    resource_id=event_resource_id(event.event_id),
                    resource_type="calendar_event",
                    scope_id=self._scope_id,
                    provider_id=ICS_PROVIDER_ID,
                    title=event.title,
                    locator=None,
                    capabilities=(),
                    observed_at=now,
                    metadata=(
                        ("start_at", event.start_at.isoformat()),
                        ("end_at", event.end_at.isoformat() if event.end_at else ""),
                        ("location", event.location),
                        ("attendees", str(len(event.attendees))),
                        ("event_id", event.event_id),
                    ),
                )
            )
        return {
            "ok": True,
            "sources": list(provider.paths),
            "events": [
                {
                    "event_id": event.event_id,
                    "title": event.title,
                    "start_at": event.start_at.isoformat(),
                    "end_at": event.end_at.isoformat() if event.end_at else None,
                    "location": event.location,
                    "attendees": list(event.attendees),
                    "resource_id": event_resource_id(event.event_id),
                }
                for event in events
            ],
        }

    def email_status(self) -> dict:
        capabilities = self.email_provider().capabilities()
        return {
            "ok": True,
            "provider": EMAIL_PROVIDER_ID,
            "configured": capabilities.read,
            "capabilities": {
                "read": capabilities.read,
                "send": capabilities.send,
                "mutate": capabilities.mutate,
            },
            "detail": capabilities.detail,
        }

    def list_messages(self, *, limit: int = 100) -> dict:
        status = self.email_status()
        if not status["capabilities"]["read"]:
            # Spread order matters: status.ok is True; the unavailability
            # answer must stay the envelope's verdict.
            return {
                "ok": False,
                "error": status["detail"],
                "provider": status["provider"],
                "configured": False,
                "capabilities": status["capabilities"],
                "detail": status["detail"],
            }
        provider = self.email_provider()
        messages = provider.messages(limit=limit)
        now = self._clock()
        for message in messages:
            self._resources.save(
                ResourceRecord(
                    resource_id=f"email:{message.message_id.strip('<>')}",
                    resource_type="email_message",
                    scope_id=self._scope_id,
                    provider_id=EMAIL_PROVIDER_ID,
                    title=message.subject,
                    locator=None,
                    capabilities=(),
                    observed_at=now,
                    metadata=(
                        ("sender", message.sender),
                        ("at", message.at),
                        ("labels", ", ".join(message.labels)),
                        ("thread", message.thread_id.strip("<>")),
                    ),
                )
            )
        return {
            "ok": True,
            **status,
            "messages": [
                {
                    "message_id": message.message_id,
                    "sender": message.sender,
                    "recipients": list(message.recipients),
                    "subject": message.subject,
                    "at": message.at,
                    "thread_id": message.thread_id,
                    "labels": list(message.labels),
                    "snippet": message.snippet,
                }
                for message in messages
            ],
        }

    # -- task proposals (never auto-created) -------------------------------------------

    def propose_task(self, *, event_id: str | None, visible) -> dict:
        if not isinstance(event_id, str) or not event_id.strip():
            return {"ok": False, "error": "a non-empty 'event_id' is required"}
        provider = self.calendar_provider()
        event = provider.get(event_id.strip())
        if event is None:
            return {"ok": False, "error": f"unknown calendar event: {event_id}"}
        result = self._tasks.create(
            visible,
            scope_id=self._scope_id,
            title=f"Follow up: {event.title}",
            created_by=self._identity.principal_id,
            due_at=event.start_at,
            source_refs=(event_resource_id(event.event_id),),
            state="proposed",
        )
        if not result.get("ok"):
            return result
        return {"ok": True, "task": result["task"]}

    # -- governed calendar mutations -----------------------------------------------------

    def create_event(
        self, *, title: str | None, start_at, end_at=None, location: str = "", attendees=()
    ) -> dict:
        if not isinstance(title, str) or not title.strip():
            return {"ok": False, "error": "a non-empty 'title' is required"}
        if not self._config["calendar_sources"]:
            return {"ok": False, "error": "no calendar source is configured"}
        event = CalendarEvent(
            event_id=f"event-{uuid4().hex}",
            title=title.strip(),
            start_at=start_at,
            end_at=end_at,
            attendees=tuple(attendees),
            location=location.strip(),
        )
        return self._request(
            "calendar.event.create",
            event_payload={
                "event_id": event.event_id,
                "title": event.title,
                "start_at": event.start_at.isoformat(),
                "end_at": event.end_at.isoformat() if event.end_at else None,
                "location": event.location,
                "attendees": list(event.attendees),
            },
        )

    def update_event(self, *, event_id: str | None, **changes) -> dict:
        provider = self.calendar_provider()
        event = provider.get(event_id.strip()) if isinstance(event_id, str) else None
        if event is None:
            return {"ok": False, "error": f"unknown calendar event: {event_id}"}
        updated = CalendarEvent(
            event_id=event.event_id,
            title=str(changes.get("title") or event.title),
            start_at=changes.get("start_at") or event.start_at,
            end_at=changes.get("end_at") if "end_at" in changes else event.end_at,
            attendees=tuple(changes.get("attendees") or event.attendees),
            location=str(changes.get("location") if changes.get("location") is not None else event.location),
        )
        return self._request(
            "calendar.event.update",
            event_payload={
                "event_id": updated.event_id,
                "title": updated.title,
                "start_at": updated.start_at.isoformat(),
                "end_at": updated.end_at.isoformat() if updated.end_at else None,
                "location": updated.location,
                "attendees": list(updated.attendees),
            },
        )

    def delete_event(self, *, event_id: str | None) -> dict:
        provider = self.calendar_provider()
        event = provider.get(event_id.strip()) if isinstance(event_id, str) else None
        if event is None:
            return {"ok": False, "error": f"unknown calendar event: {event_id}"}
        return self._request("calendar.event.delete", event_payload={"event_id": event.event_id})

    def confirm(self, *, request_id: str | None) -> dict:
        return self._confirm(request_id)

    def deny(self, *, request_id: str | None) -> dict:
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

    def _request(self, action: str, *, event_payload: dict) -> dict:
        principal = self._identity.current_principal()
        now = self._clock()
        request = ResourceActionRequest(
            request_id=_new_id("action"),
            household_id=principal.household_id,
            requested_by=principal.actor_id,
            provider_id=ICS_PROVIDER_ID,
            action=action,
            resource_id=event_resource_id(event_payload["event_id"]),
            parameters=tuple(sorted((key, json.dumps(value)) for key, value in event_payload.items())),
            justification="owner changed a calendar event from HAVEN",
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
                "event": event_payload,
            }
        return self._dispatch(request, decision)

    def _confirm(self, request_id: str | None) -> dict:
        if not isinstance(request_id, str) or not request_id.strip():
            return {"ok": False, "error": "a non-empty 'request_id' is required"}
        request = self._pending.get(request_id.strip())
        if request is None:
            return {"ok": False, "error": "no pending action with that request_id"}
        principal = self._identity.current_principal()
        now = self._clock()
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
        payload = {key: json.loads(value) for key, value in request.parameters}
        provider = self.calendar_provider()
        verified = None
        if request.action == "calendar.event.create":
            event = _event_from_payload(payload)
            verified = provider.create_event(event) if event is not None else None
        elif request.action == "calendar.event.update":
            event = _event_from_payload(payload)
            verified = provider.update_event(event) if event is not None else None
        else:
            verified = provider.delete_event(payload["event_id"])
        success = verified is not None and verified is not False
        # Re-fetch verification: the source file must now agree with the intent.
        detail = "verified by re-read" if success else "the calendar source did not accept the change"
        self._record(request, decision, success=success, detail=detail)
        if success:
            self.list_events()  # refresh the projection
        return {
            "ok": True,
            "success": success,
            "detail": detail,
            "event": _event_wire(verified) if isinstance(verified, CalendarEvent) else None,
        }

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


def _event_from_payload(payload: dict) -> CalendarEvent | None:
    try:
        start_at = datetime.fromisoformat(payload["start_at"])
        end_at = datetime.fromisoformat(payload["end_at"]) if payload.get("end_at") else None
    except (KeyError, ValueError, TypeError):
        return None
    return CalendarEvent(
        event_id=str(payload["event_id"]),
        title=str(payload["title"]),
        start_at=start_at,
        end_at=end_at,
        attendees=tuple(payload.get("attendees") or ()),
        location=str(payload.get("location") or ""),
    )


def _event_wire(event) -> dict:
    return {
        "event_id": event.event_id,
        "title": event.title,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat() if event.end_at else None,
        "location": event.location,
        "attendees": list(event.attendees),
        "resource_id": event_resource_id(event.event_id),
    }


__all__ = ["CommsService", "event_resource_id"]
