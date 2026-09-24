"""Browser context provider: HAVEN integrates browser context, it does not
become a browser (spec page 31).

Observation boundary: only explicitly connected browsers; no page body,
cookies, form fields, passwords, or full-history indexing; incognito tabs
are excluded at ingest. Closed tabs become stale historical resources,
never deletions. Tab identity is the extension-assigned stable tab id,
never the URL.

The connector seam is `BrowserHub`: a reference native-messaging scaffold
lives under the repo's `browser/` folder (experimental), but the runtime
never depends on it -- with no browser connected, every capability reports
explicit unavailability (NOMAD honesty).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

PROVIDER_ID = "haven.browser"

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


def tab_resource_id(tab_id: str) -> str:
    return f"browsertab:{tab_id}"


def domain_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.casefold()
    except ValueError:
        return ""


@dataclass(frozen=True)
class BrowserTabSnapshot:
    """One observed tab. `tab_id` is the browser-assigned stable identity."""

    tab_id: str
    browser: str
    title: str
    url: str
    last_active_at: datetime
    loading: bool = False
    incognito: bool = False

    def __post_init__(self) -> None:
        for field_name in ("tab_id", "browser"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.last_active_at.tzinfo is None or self.last_active_at.utcoffset() is None:
            raise ValueError("last_active_at must be timezone-aware")


@dataclass(frozen=True)
class BrowserCommandResult:
    accepted: bool
    detail: str


class BrowserHub:
    """The connector seam: browsers push observations, HAVEN pushes commands.

    A connected browser registers an ingest callback and a command sink.
    The reference native-messaging connector (`browser/` in the repo) wires
    those to the extension; tests inject fakes. The default command sink
    reports explicit unavailability.
    """

    def __init__(self, *, clock=_DEFAULT_CLOCK) -> None:
        self._clock = clock
        self._browsers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def connect_browser(
        self,
        browser: str,
        *,
        ingest,
        send_command=None,
    ) -> None:
        """A connector binds a browser's seams. `ingest(snapshots: tuple)`."""

        with self._lock:
            self._browsers[browser] = {
                "ingest": ingest,
                "send_command": send_command or self._unavailable_sink(browser),
                "connected_at": self._clock(),
            }

    def disconnect_browser(self, browser: str) -> None:
        """All that browser's tabs go stale; the browser stops answering."""

        with self._lock:
            self._browsers.pop(browser, None)

    def connected_browsers(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._browsers))

    def send_command(self, browser: str, command: str, **parameters) -> BrowserCommandResult:
        with self._lock:
            entry = self._browsers.get(browser)
        if entry is None:
            return BrowserCommandResult(False, f"no connector is connected for {browser!r}")
        try:
            return entry["send_command"](command, parameters)
        except Exception as exc:  # a broken connector must not crash HAVEN
            return BrowserCommandResult(False, f"connector error: {exc}")

    @staticmethod
    def _unavailable_sink(browser: str):
        def sink(command: str, parameters: dict) -> BrowserCommandResult:
            return BrowserCommandResult(
                False, f"the {browser} connector does not accept commands"
            )

        return sink


class BrowserObservationProvider:
    """Ingests connector snapshots and projects tabs into the ResourceStore."""

    def __init__(
        self,
        *,
        hub: BrowserHub,
        resource_store: ResourceStore,
        scope_id: str,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        self._hub = hub
        self._resources = resource_store
        self._scope_id = scope_id
        self._clock = clock
        self._tabs: dict[str, BrowserTabSnapshot] = {}
        self._lock = threading.Lock()

    @property
    def scope_id(self) -> str:
        return self._scope_id

    def ingest_tabs(self, browser: str, snapshots) -> dict:
        """Connector entry point. Incognito tabs are excluded, not recorded."""

        accepted: list[str] = []
        excluded = 0
        with self._lock:
            for snapshot in snapshots:
                if not isinstance(snapshot, BrowserTabSnapshot):
                    raise ValueError("snapshots must be BrowserTabSnapshot records")
                if snapshot.browser != browser:
                    raise ValueError("snapshot browser does not match the connected browser")
                if snapshot.incognito:
                    excluded += 1
                    continue
                self._tabs[snapshot.tab_id] = snapshot
                accepted.append(snapshot.tab_id)
        return {"ok": True, "accepted": accepted, "incognito_excluded": excluded}

    def mark_tab_closed(self, tab_id: str) -> None:
        with self._lock:
            self._tabs.pop(tab_id, None)

    def list_tabs(self) -> tuple[BrowserTabSnapshot, ...]:
        with self._lock:
            return tuple(
                sorted(self._tabs.values(), key=lambda tab: tab.last_active_at, reverse=True)
            )

    def get_tab(self, tab_id: str) -> BrowserTabSnapshot | None:
        with self._lock:
            return self._tabs.get(tab_id)

    def status(self) -> dict:
        return {
            "connected_browsers": list(self._hub.connected_browsers()),
            "tabs": len(self._tabs),
        }

    def observe_and_project(self) -> tuple[BrowserTabSnapshot, ...]:
        """Fold the current tab set into the ResourceStore; closed -> stale."""

        now = self._clock()
        tabs = self.list_tabs()
        seen_ids = {tab_resource_id(tab.tab_id) for tab in tabs}
        for tab in tabs:
            self._resources.save(
                ResourceRecord(
                    resource_id=tab_resource_id(tab.tab_id),
                    resource_type="browser_tab",
                    scope_id=self._scope_id,
                    provider_id=PROVIDER_ID,
                    title=tab.title or tab.url,
                    locator=None,
                    capabilities=("browser.tab.focus",),
                    observed_at=now,
                    metadata=(
                        ("browser", tab.browser),
                        ("domain", domain_of(tab.url)),
                        ("url", tab.url),
                        ("loading", str(tab.loading).lower()),
                        ("tab_id", tab.tab_id),
                    ),
                )
            )
        for record in self._resources.list_by_scope(self._scope_id):
            if record.provider_id != PROVIDER_ID or record.resource_type != "browser_tab":
                continue
            if record.resource_id not in seen_ids and not record.stale:
                self._resources.save(
                    ResourceRecord(
                        resource_id=record.resource_id,
                        resource_type=record.resource_type,
                        scope_id=record.scope_id,
                        provider_id=record.provider_id,
                        title=record.title,
                        locator=record.locator,
                        capabilities=record.capabilities,
                        observed_at=record.observed_at,
                        content_hash=record.content_hash,
                        metadata=record.metadata,
                        stale=True,
                    )
                )
        return tabs


__all__ = [
    "BrowserCommandResult",
    "BrowserHub",
    "BrowserObservationProvider",
    "BrowserTabSnapshot",
    "PROVIDER_ID",
    "domain_of",
    "tab_resource_id",
]
