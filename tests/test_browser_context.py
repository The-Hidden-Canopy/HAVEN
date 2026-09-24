"""Browser context: observation boundary, projection, and the governed action tiers."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.actions import ActionLedgerStore
from haven.integrations.browser import (
    BrowserCommandResult,
    BrowserHub,
    BrowserObservationProvider,
    BrowserTabSnapshot,
    tab_resource_id,
)
from haven.resources.store import ResourceStore
from haven.web.browser_actions import BrowserActionService

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class _FakeDirector:
    household_id = "household-authoring"
    has_declared_owner = True

    class _Principal:
        actor_id = "gerron"
        household_id = "household-authoring"
        role_tier = None

    resident = _Principal()

    def __init__(self) -> None:
        from haven.core.domain import RoleTier

        self.resident.role_tier = RoleTier.OWNER


TAB = BrowserTabSnapshot(
    tab_id="tab-1",
    browser="chrome",
    title="UNLV Proposal - Google Docs",
    url="https://docs.google.com/document/d/1",
    last_active_at=NOW,
)
TAB_INCOGNITO = BrowserTabSnapshot(
    tab_id="tab-9",
    browser="chrome",
    title="private",
    url="https://example.org",
    last_active_at=NOW,
    incognito=True,
)


@pytest.fixture()
def stack():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        hub = BrowserHub(clock=lambda: NOW)
        provider = BrowserObservationProvider(
            hub=hub, resource_store=resources, scope_id="scope:personal", clock=lambda: NOW
        )
        ledger = ActionLedgerStore(Path(tmp) / "ledger.db")
        service = BrowserActionService(
            director=_FakeDirector(),
            provider=provider,
            resource_store=resources,
            ledger=ledger,
            clock=lambda: NOW,
        )
        commands: list[tuple[str, dict]] = []
        hub.connect_browser(
            "chrome",
            ingest=lambda snapshots: provider.ingest_tabs("chrome", snapshots),
            send_command=lambda command, params: commands.append((command, params))
            or BrowserCommandResult(True, "ok"),
        )
        provider.ingest_tabs("chrome", (TAB,))
        yield provider, service, resources, ledger, commands, hub


def test_ingest_excludes_incognito_and_projects(stack) -> None:
    provider, _service, resources, _ledger, _commands, _hub = stack
    result = provider.ingest_tabs("chrome", (TAB, TAB_INCOGNITO))
    assert result["incognito_excluded"] == 1
    assert result["accepted"] == ["tab-1"]

    provider.observe_and_project()
    record = resources.get(tab_resource_id("tab-1"))
    assert record is not None
    assert record.resource_type == "browser_tab"
    assert dict(record.metadata)["domain"] == "docs.google.com"
    assert resources.get(tab_resource_id("tab-9")) is None

    # Closing the tab stales the resource; identity survives, history stays.
    provider.mark_tab_closed("tab-1")
    provider.observe_and_project()
    assert resources.get(tab_resource_id("tab-1")).stale is True


def test_no_connector_means_explicit_unavailable(stack) -> None:
    provider, _service, resources, _ledger, _commands, hub = stack
    hub.disconnect_browser("chrome")
    provider.ingest_tabs("chrome", (TAB,))
    provider.observe_and_project()
    assert resources.get(tab_resource_id("tab-1")) is not None  # observation still works

    # A second browser never connected: commands refuse honestly.
    other = BrowserTabSnapshot(
        tab_id="tab-2", browser="firefox", title="x", url="https://x.org", last_active_at=NOW
    )
    provider.ingest_tabs("firefox", (other,))
    provider.observe_and_project()
    service = BrowserActionService(
        director=_FakeDirector(),
        provider=provider,
        resource_store=resources,
        ledger=ActionLedgerStore(Path(tempfile.mkdtemp()) / "l.db"),
        clock=lambda: NOW,
    )
    denied = service.focus_tab(resource_id=tab_resource_id("tab-2"))
    # The request itself is allowed; execution reports the missing connector
    # in the consequence slot -- explicit, receipted, never a silent no-op.
    assert denied["ok"] is True
    assert denied["success"] is False
    assert "no connector" in denied["detail"]


def test_focus_and_open_are_direct_receipted(stack) -> None:
    _provider, service, _resources, ledger, commands, _hub = stack
    focused = service.focus_tab(resource_id=tab_resource_id("tab-1"))
    assert focused == {"ok": True, "success": True, "detail": "ok"}
    assert commands[-1] == ("focus_tab", {"tab_id": "tab-1"})

    opened = service.open_url(browser="chrome", url="https://haven.example.org")
    assert opened["success"] is True
    assert commands[-1] == ("open_url", {"url": "https://haven.example.org"})

    entries = ledger.list_by_household("household-authoring")
    assert {entry.action for entry in entries} == {"browser.tab.focus", "browser.tab.open"}
    assert all(entry.success for entry in entries)


def test_close_is_confirmation_gated_and_deny_releases(stack) -> None:
    _provider, service, resources, ledger, commands, _hub = stack
    requested = service.close_tab(resource_id=tab_resource_id("tab-1"))
    assert requested["status"] == "confirmation_required"
    assert resources.get(tab_resource_id("tab-1")).stale is False  # nothing happened yet

    denied = service.deny_close(request_id=requested["request_id"])
    assert denied["ok"] is True
    assert commands == []  # no close reached the browser
    (entry,) = [
        item
        for item in ledger.list_by_household("household-authoring")
        if item.action == "browser.tab.close" and item.status.value == "deny"
    ]
    assert entry.status.value == "deny"

    requested = service.close_tab(resource_id=tab_resource_id("tab-1"))
    confirmed = service.confirm_close(request_id=requested["request_id"])
    assert confirmed["success"] is True
    assert commands[-1] == ("close_tab", {"tab_id": "tab-1"})


def test_fail_closed_without_owner_and_on_unknown_tabs(stack) -> None:
    _provider, service, resources, _ledger, _commands, _hub = stack
    service.set_director(type("D", (), {"has_declared_owner": False, "resident": _FakeDirector.resident, "household_id": "household-authoring"})())
    assert service.focus_tab(resource_id=tab_resource_id("tab-1"))["ok"] is False
    assert service.open_url(browser="chrome", url="https://x.org")["ok"] is False

    service.set_director(_FakeDirector())
    assert service.focus_tab(resource_id="browsertab:ghost")["ok"] is False
    resources.save(
        type(resources.get(tab_resource_id("tab-1")))(
            resource_id="file:notatab",
            resource_type="file",
            scope_id="scope:personal",
            provider_id="local_filesystem",
            title="x",
            locator=None,
            capabilities=(),
            observed_at=NOW,
        )
    )
    assert service.focus_tab(resource_id="file:notatab")["ok"] is False
