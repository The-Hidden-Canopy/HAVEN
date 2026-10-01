"""Comms service: config, projection, governed calendar mutations, proposals.

External sources stay authoritative: a governed calendar write is executed
by the adapter and then verified by re-reading the same source. Events can
attach to projects (relationship assertions) and propose tasks -- PROPOSED,
never auto-created (spec page 32).
"""

from __future__ import annotations

import json
import hashlib
import math
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from haven.credentials import CredentialKind, CredentialStore, UnknownCredentialError
from haven.automation.deadlines import AutomationDeadline
from haven.actions import (
    ActionLedgerEntry,
    ActionLedgerStore,
    ResourceActionDecision,
    ResourceActionRequest,
    ResourceAuthorityEngine,
)
from haven.core import correlation
from haven.core.domain import ConfirmationToken, DecisionStatus, EvidenceStatus, RiskTier
from haven.integrations.comms import (
    EMAIL_PROVIDER_ID,
    IMAP_SMTP_PROVIDER_ID,
    CredentialEmailProvider,
    ICS_PROVIDER_ID,
    CalendarEvent,
    CompositeCalendarProvider,
    LocalIcsCalendarProvider,
    REMOTE_ICS_PROVIDER_ID,
    RemoteIcsCalendarProvider,
    LocalMaildirProvider,
    EmailProviderError,
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
        credential_store: CredentialStore | None = None,
        message_event_listener=None,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        self._config_path = Path(config_path)
        self._resources = resource_store
        self._ledger = ledger
        self._tasks = tasks_service
        self._identity = identity
        self._scope_id = scope_id
        self._credentials = credential_store
        self._message_event_listener = message_event_listener
        self._clock = clock
        self._lock = threading.Lock()
        self._engine = ResourceAuthorityEngine(
            action_risk={
                "calendar.event.create": RiskTier.CONFIRMATION_REQUIRED,
                "calendar.event.update": RiskTier.CONFIRMATION_REQUIRED,
                "calendar.event.delete": RiskTier.CONFIRMATION_REQUIRED,
                "email.message.send": RiskTier.CONFIRMATION_REQUIRED,
            }
        )
        self._pending: dict[str, ResourceActionRequest] = {}
        self._config = self._load_config()

    # -- configuration ----------------------------------------------------------

    def _load_config(self) -> dict:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"calendar_sources": [], "calendar_remotes": [], "maildir": None, "email": None}
        if not isinstance(data, dict):
            return {"calendar_sources": [], "calendar_remotes": [], "maildir": None, "email": None}
        email_config = data.get("email")
        if not isinstance(email_config, dict):
            email_config = None
        calendar_remotes = []
        for item in data.get("calendar_remotes", []):
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            credential_id = item.get("credential_id")
            if isinstance(url, str) and url.strip() and isinstance(credential_id, str) and credential_id.strip():
                try:
                    timeout = float(item.get("timeout") or 10.0)
                except (TypeError, ValueError):
                    timeout = 10.0
                calendar_remotes.append(
                    {
                        "url": url.strip(),
                        "username": str(item.get("username") or "").strip(),
                        "auth_mode": str(item.get("auth_mode") or "bearer"),
                        "credential_id": credential_id.strip(),
                        "timeout": timeout,
                    }
                )
        return {
            "calendar_sources": [
                str(item) for item in data.get("calendar_sources", []) if isinstance(item, str)
            ],
            "calendar_remotes": calendar_remotes,
            "maildir": data.get("maildir") if isinstance(data.get("maildir"), str) else None,
            "email": email_config,
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

    def configure_calendar(
        self,
        *,
        url: str | None,
        secret: str | None,
        username: str = "",
        auth_mode: str = "bearer",
        timeout: float = 10.0,
    ) -> dict:
        """Persist remote-feed metadata and its secret by credential reference."""

        if self._credentials is None:
            return {"ok": False, "error": "credential storage is unavailable"}
        if not isinstance(url, str) or not url.strip():
            return {"ok": False, "error": "a non-empty 'url' is required"}
        try:
            parsed = urllib.parse.urlparse(url.strip())
        except ValueError:
            parsed = None
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"ok": False, "error": "url must use http or https"}
        if not isinstance(secret, str) or not secret:
            return {"ok": False, "error": "a non-empty calendar secret is required"}
        if not isinstance(username, str):
            return {"ok": False, "error": "username must be a string"}
        if auth_mode not in {"bearer", "basic"}:
            return {"ok": False, "error": "auth_mode must be 'bearer' or 'basic'"}
        if auth_mode == "basic" and (not isinstance(username, str) or not username.strip()):
            return {"ok": False, "error": "username is required for basic authentication"}
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or not 0 < float(timeout) <= 60
        ):
            return {"ok": False, "error": "timeout must be between 0 and 60 seconds"}
        normalized_url = url.strip()
        credential_base = "comms.calendar." + hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:24]
        try:
            metadata = self._credentials.list_metadata(provider=REMOTE_ICS_PROVIDER_ID, include_revoked=True)
            active = next((item for item in metadata if item.credential_id == credential_base and not item.revoked), None)
            credential_id = credential_base if active is not None else credential_base
            if any(item.credential_id == credential_base and item.revoked for item in metadata):
                credential_id = credential_base + "." + uuid4().hex[:8]
            if active is not None:
                self._credentials.rotate(credential_id, new_secret=secret)
            else:
                self._credentials.create(
                    credential_id=credential_id,
                    provider=REMOTE_ICS_PROVIDER_ID,
                    account_label=(username.strip() or normalized_url),
                    kind=CredentialKind.USER,
                    secret=secret,
                )
        except Exception as exc:
            return {"ok": False, "error": f"calendar credential could not be stored: {exc}"}
        self._config["calendar_remotes"] = [
            item for item in self._config["calendar_remotes"] if item.get("url") != normalized_url
        ]
        self._config["calendar_remotes"].append(
            {
                "url": normalized_url,
                "username": username.strip(),
                "auth_mode": auth_mode,
                "credential_id": credential_id,
                "timeout": float(timeout),
            }
        )
        self._save_config()
        return {
            "ok": True,
            "provider": REMOTE_ICS_PROVIDER_ID,
            "url": normalized_url,
            "auth_mode": auth_mode,
        }

    def remove_calendar_remote(self, url: str) -> dict:
        if not isinstance(url, str) or not url.strip():
            return {"ok": False, "error": "a non-empty 'url' is required"}
        normalized_url = url.strip()
        removed = [item for item in self._config["calendar_remotes"] if item.get("url") == normalized_url]
        self._config["calendar_remotes"] = [
            item for item in self._config["calendar_remotes"] if item.get("url") != normalized_url
        ]
        for item in removed:
            credential_id = item.get("credential_id")
            if self._credentials is not None and isinstance(credential_id, str):
                try:
                    self._credentials.revoke(credential_id)
                except UnknownCredentialError:
                    pass
        self._save_config()
        return {"ok": True, "calendar_remotes": self._calendar_remote_wire()}

    def set_maildir(self, path: str | None) -> dict:
        with self._lock:
            self._config["maildir"] = path.strip() if isinstance(path, str) and path.strip() else None
            self._save_config()
        return {"ok": True, "maildir": self._config["maildir"]}

    def configure_email(
        self,
        *,
        imap_host: str | None,
        username: str | None,
        secret: str | None,
        imap_port: int = 993,
        smtp_host: str | None = None,
        smtp_port: int = 465,
        mailbox: str = "INBOX",
    ) -> dict:
        """Persist only email connection metadata; store the secret in DPAPI."""

        if self._credentials is None:
            return {"ok": False, "error": "credential storage is unavailable"}
        if not isinstance(imap_host, str) or not imap_host.strip():
            return {"ok": False, "error": "a non-empty 'imap_host' is required"}
        if not isinstance(username, str) or not username.strip():
            return {"ok": False, "error": "a non-empty 'username' is required"}
        if not isinstance(secret, str) or not secret:
            return {"ok": False, "error": "a non-empty email secret is required"}
        if isinstance(imap_port, bool) or not isinstance(imap_port, int) or not 1 <= imap_port <= 65535:
            return {"ok": False, "error": "imap_port must be between 1 and 65535"}
        if isinstance(smtp_port, bool) or not isinstance(smtp_port, int) or not 1 <= smtp_port <= 65535:
            return {"ok": False, "error": "smtp_port must be between 1 and 65535"}
        if not isinstance(mailbox, str) or not mailbox.strip():
            return {"ok": False, "error": "mailbox must be a non-empty string"}
        credential_id = "comms.email.password"
        try:
            self._credentials.rotate(credential_id, new_secret=secret)
        except UnknownCredentialError:
            self._credentials.create(
                credential_id=credential_id,
                provider=IMAP_SMTP_PROVIDER_ID,
                account_label=username.strip(),
                kind=CredentialKind.USER,
                secret=secret,
            )
        self._config["email"] = {
            "imap_host": imap_host.strip(),
            "imap_port": imap_port,
            "smtp_host": (smtp_host.strip() if isinstance(smtp_host, str) and smtp_host.strip() else imap_host.strip()),
            "smtp_port": smtp_port,
            "username": username.strip(),
            "mailbox": mailbox.strip(),
            "credential_id": credential_id,
        }
        self._config["maildir"] = None
        self._save_config()
        return {"ok": True, "provider": IMAP_SMTP_PROVIDER_ID, "username": username.strip()}

    # -- provider seams ------------------------------------------------------------

    def _calendar_remote_wire(self) -> list[dict]:
        return [
            {
                "url": item["url"],
                "username": item.get("username", ""),
                "auth_mode": item.get("auth_mode", "bearer"),
                "timeout": item.get("timeout", 10.0),
            }
            for item in self._config["calendar_remotes"]
        ]

    def _calendar_remote_providers(self) -> tuple[RemoteIcsCalendarProvider, ...]:
        if self._credentials is None:
            return ()
        try:
            metadata = self._credentials.list_metadata(provider=REMOTE_ICS_PROVIDER_ID, include_revoked=False)
        except Exception:
            return ()
        active = {item.credential_id for item in metadata}
        providers = []
        for item in self._config["calendar_remotes"]:
            credential_id = item.get("credential_id")
            if credential_id not in active:
                continue
            try:
                providers.append(
                    RemoteIcsCalendarProvider(
                        url=item["url"],
                        username=item.get("username", ""),
                        auth_mode=item.get("auth_mode", "bearer"),
                        timeout=item.get("timeout", 10.0),
                        secret_loader=lambda credential_id=credential_id: self._credentials.get_secret(credential_id),
                    )
                )
            except ValueError:
                continue
        return tuple(providers)

    def calendar_provider(self) -> CompositeCalendarProvider:
        return CompositeCalendarProvider(
            LocalIcsCalendarProvider(self._config["calendar_sources"]),
            self._calendar_remote_providers(),
        )

    def calendar_status(self, *, errors: tuple[str, ...] = ()) -> dict:
        configured_remotes = self._config["calendar_remotes"]
        try:
            active_remote_ids = {
                item.credential_id
                for item in (
                    self._credentials.list_metadata(provider=REMOTE_ICS_PROVIDER_ID, include_revoked=False)
                    if self._credentials
                    else ()
                )
            }
        except Exception:
            active_remote_ids = set()
        sources = [
            {"kind": "local_ics", "source": path, "configured": True, "read": True, "mutate": True}
            for path in self._config["calendar_sources"]
        ]
        for item in configured_remotes:
            credential_available = item["credential_id"] in active_remote_ids
            sources.append(
                {
                    "kind": "remote_ics",
                    "source": item["url"],
                    "configured": credential_available,
                    "read": credential_available,
                    "mutate": False,
                    "detail": "credential reference is unavailable" if not credential_available else "reachability checked on read",
                }
            )
        return {
            "ok": True,
            "configured": bool(sources),
            "provider": REMOTE_ICS_PROVIDER_ID if configured_remotes and not self._config["calendar_sources"] else ICS_PROVIDER_ID,
            "sources": sources,
            "errors": list(errors),
            "detail": "one or more calendar sources are unavailable" if errors else "calendar sources are configured",
        }

    def automation_deadlines(self, *, household_id: str) -> tuple[AutomationDeadline, ...]:
        """Project authoritative calendar starts into the automation seam.

        Local files and credentialed remote feeds are read through at
        projection time and marked observed. Missing, unreadable, unreachable,
        or credential-unavailable sources produce no runnable signal; source
        health is published separately by ``calendar.status``.
        """

        seen: dict[str, CalendarEvent] = {}
        for event in self.calendar_provider().events():
            seen.setdefault(event.event_id, event)
        return tuple(
            AutomationDeadline(
                deadline_id=f"calendar:{event.event_id}",
                household_id=household_id,
                source_kind="calendar_event",
                source_id=event.event_id,
                due_at=event.start_at,
                payload={
                    "event_id": event.event_id,
                    "title": event.title,
                    "provider_id": event.provider_id,
                    "location": event.location,
                    "has_end": event.end_at is not None,
                },
                evidence_status=EvidenceStatus.OBSERVED,
            )
            for event in seen.values()
        )

    def email_provider(self):
        email_config = self._config.get("email")
        if isinstance(email_config, dict) and self._credentials is not None:
            credential_id = email_config.get("credential_id")
            try:
                metadata = self._credentials.list_metadata(provider=IMAP_SMTP_PROVIDER_ID, include_revoked=False)
            except Exception:
                return UnconfiguredEmailProvider(detail="email credential metadata is unavailable")
            if isinstance(credential_id, str) and credential_id and any(
                item.credential_id == credential_id for item in metadata
            ):
                return CredentialEmailProvider(
                    imap_host=str(email_config.get("imap_host") or ""),
                    imap_port=int(email_config.get("imap_port") or 993),
                    smtp_host=str(email_config.get("smtp_host") or email_config.get("imap_host") or ""),
                    smtp_port=int(email_config.get("smtp_port") or 465),
                    username=str(email_config.get("username") or ""),
                    mailbox=str(email_config.get("mailbox") or "INBOX"),
                    secret_loader=lambda: self._credentials.get_secret(credential_id),
                )
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
                    provider_id=event.provider_id,
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
            "status": self.calendar_status(errors=provider.errors),
            "source_errors": list(provider.errors),
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
        provider = self.email_provider()
        capabilities = provider.capabilities()
        return {
            "ok": True,
            "provider": getattr(provider, "provider_id", EMAIL_PROVIDER_ID),
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
        try:
            messages = provider.messages(limit=limit)
        except (EmailProviderError, OSError, UnknownCredentialError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "provider": status["provider"],
                "configured": True,
                "capabilities": status["capabilities"],
                "detail": "email provider unavailable while reading the mailbox",
            }
        now = self._clock()
        if self._message_event_listener is not None:
            try:
                self._message_event_listener(
                    messages,
                    is_new=lambda message_id: self._resources.get(f"email:{message_id}") is None,
                    occurred_at=now,
                )
            except Exception:
                # Event consumers are advisory; a mailbox read that
                # succeeded must remain a successful read if delivery fails.
                pass
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

    def send_message(
        self,
        *,
        recipients,
        subject: str | None,
        body: str | None,
        cc=(),
        justification: str | None = None,
    ) -> dict:
        """Request a governed outbound message; the provider sends only after confirmation."""

        if not isinstance(recipients, (list, tuple)) or not recipients:
            return {"ok": False, "error": "at least one recipient is required"}
        if any(not isinstance(item, str) or not item.strip() for item in recipients):
            return {"ok": False, "error": "recipients must be non-empty strings"}
        if not isinstance(subject, str) or not subject.strip():
            return {"ok": False, "error": "a non-empty 'subject' is required"}
        if not isinstance(body, str) or not body.strip():
            return {"ok": False, "error": "a non-empty 'body' is required"}
        if not isinstance(cc, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in cc):
            return {"ok": False, "error": "cc must be a list of non-empty strings"}
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "email send requires a non-empty justification"}
        status = self.email_status()
        if not status["capabilities"]["send"]:
            return {"ok": False, "error": status["detail"]}
        message_id = _new_id("outbound")
        return self._request(
            "email.message.send",
            provider_id=IMAP_SMTP_PROVIDER_ID,
            resource_id=f"email:{message_id}",
            event_payload={
                "message_id": message_id,
                "recipients": [item.strip() for item in recipients],
                "cc": [item.strip() for item in cc],
                "subject": subject.strip(),
                "body": body,
            },
            justification=justification.strip(),
        )

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
        self,
        *,
        title: str | None,
        start_at,
        end_at=None,
        location: str = "",
        attendees=(),
        justification: str | None = None,
    ) -> dict:
        if not isinstance(title, str) or not title.strip():
            return {"ok": False, "error": "a non-empty 'title' is required"}
        if not self._config["calendar_sources"]:
            return {"ok": False, "error": "no calendar source is configured"}
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "calendar create requires a non-empty justification"}
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
            justification=justification.strip(),
        )

    def update_event(self, *, event_id: str | None, justification: str | None = None, **changes) -> dict:
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "calendar update requires a non-empty justification"}
        provider = self.calendar_provider()
        event = provider.get(event_id.strip()) if isinstance(event_id, str) else None
        if event is None:
            return {"ok": False, "error": f"unknown calendar event: {event_id}"}
        if event.provider_id != ICS_PROVIDER_ID:
            return {"ok": False, "error": "remote calendar events are read-only"}
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
            justification=justification.strip(),
        )

    def delete_event(self, *, event_id: str | None, justification: str | None = None) -> dict:
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "calendar delete requires a non-empty justification"}
        provider = self.calendar_provider()
        event = provider.get(event_id.strip()) if isinstance(event_id, str) else None
        if event is None:
            return {"ok": False, "error": f"unknown calendar event: {event_id}"}
        if event.provider_id != ICS_PROVIDER_ID:
            return {"ok": False, "error": "remote calendar events are read-only"}
        return self._request(
            "calendar.event.delete",
            event_payload={"event_id": event.event_id},
            justification=justification.strip(),
        )

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

    def _request(
        self,
        action: str,
        *,
        event_payload: dict,
        justification: str,
        provider_id: str = ICS_PROVIDER_ID,
        resource_id: str | None = None,
    ) -> dict:
        principal = self._identity.current_principal()
        now = self._clock()
        request = ResourceActionRequest(
            request_id=_new_id("action"),
            household_id=principal.household_id,
            requested_by=principal.actor_id,
            provider_id=provider_id,
            action=action,
            resource_id=resource_id or event_resource_id(event_payload["event_id"]),
            parameters=tuple(sorted((key, json.dumps(value)) for key, value in event_payload.items())),
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
        if request.action == "email.message.send":
            provider = self.email_provider()
            try:
                message_id = provider.send(
                    recipients=tuple(payload["recipients"]),
                    cc=tuple(payload.get("cc") or ()),
                    subject=payload["subject"],
                    body=payload["body"],
                )
            except (EmailProviderError, OSError, UnknownCredentialError) as exc:
                self._record(request, decision, success=False, detail=str(exc))
                return {"ok": True, "success": False, "detail": str(exc), "message_id": None}
            self._record(request, decision, success=True, detail="accepted by SMTP provider")
            return {"ok": True, "success": True, "detail": "accepted by SMTP provider", "message_id": message_id}

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
                correlation_id=correlation.current(),
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
