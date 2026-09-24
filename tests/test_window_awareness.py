"""Application/window awareness: observation, suppression, retention, and the
governed focus action (hermetic via injected win32 seams + one real-machine
enumeration test)."""

from __future__ import annotations

import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.actions import ActionLedgerStore
from haven.integrations.computer.windows import (
    PROVIDER_ID,
    WindowObservationProvider,
    WindowSnapshot,
    window_resource_id,
)
from haven.resources.store import ResourceStore
from haven.web.window_actions import WindowActionService

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class _FakeDirector:
    household_id = "household-authoring"

    class _Principal:
        actor_id = "gerron"
        household_id = "household-authoring"
        role_tier = None  # filled below

    resident = _Principal()
    has_declared_owner = True

    def __init__(self) -> None:
        from haven.core.domain import RoleTier

        self.resident.role_tier = RoleTier.OWNER


@pytest.fixture()
def provider():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        yield resources


def _make(resources, *, windows=(), foreground=0, focused=None, retention=5):
    state = {"windows": list(windows), "foreground": foreground, "focused": []}
    provider = WindowObservationProvider(
        resource_store=resources,
        scope_id="scope:personal",
        clock=lambda: NOW,
        retention=retention,
        enum_windows=lambda: tuple(state["windows"]),
        foreground_hwnd=lambda: state["foreground"],
        bring_to_foreground=lambda hwnd: state["focused"].append(hwnd) or True,
        poll_seconds=0.05,
    )
    return provider, state


SNAPSHOT_A = WindowSnapshot(hwnd=101, title="Proposal.docx - Word", process_id=10, process_name="WINWORD.EXE")
SNAPSHOT_B = WindowSnapshot(hwnd=202, title="HAVEN console", process_id=20, process_name="python.exe")


def test_enumeration_projects_and_closed_windows_go_stale(provider) -> None:
    resources = provider
    observation, _state = _make(resources, windows=(SNAPSHOT_A, SNAPSHOT_B))
    observation.observe_and_project()

    record = resources.get(window_resource_id(101))
    assert record is not None
    assert record.resource_type == "window"
    assert record.provider_id == PROVIDER_ID
    assert record.scope_id == "scope:personal"
    assert record.title == "Proposal.docx - Word"
    assert dict(record.metadata)["process"] == "WINWORD.EXE"
    assert "window.focus" in record.capabilities

    # The window closes: the next pass marks it stale instead of deleting it.
    observation, _state = _make(resources, windows=(SNAPSHOT_B,))
    observation.observe_and_project()
    assert resources.get(window_resource_id(101)).stale is True
    assert resources.get(window_resource_id(202)).stale is False


def test_apps_listing_distinguishes_apps(provider) -> None:
    observation, _state = _make(
        provider, windows=(SNAPSHOT_A, SNAPSHOT_B, WindowSnapshot(hwnd=303, title="Other.docx - Word", process_id=11, process_name="WINWORD.EXE"))
    )
    apps = {row["app"]: row for row in observation.apps()}
    assert apps["WINWORD.EXE"]["windows"] == 2
    assert apps["python.exe"]["windows"] == 1


def test_foreground_observation_is_opt_in_with_suppression_and_retention(provider) -> None:
    observation, state = _make(
        provider,
        windows=(SNAPSHOT_A, SNAPSHOT_B),
        retention=3,
        foreground=101,
    )
    # Disabled by default: nothing is recorded, even when the foreground moves.
    state["foreground"] = 202
    time.sleep(0.2)
    assert observation.activity() == ()

    observation.set_suppressed("WINWORD.EXE", suppressed=True)
    observation.set_observation(True)
    try:
        for hwnd, expected in ((202, "python.exe"), (101, None), (202, "python.exe"), (101, None), (202, "python.exe")):
            state["foreground"] = hwnd
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                time.sleep(0.05)
                if expected is None:
                    if all(event.app != "WINWORD.EXE" for event in observation._history):  # noqa: SLF001
                        break
                elif any(event.app == expected for event in observation._history):  # noqa: SLF001
                    break
        events = observation.activity()
        assert events, "foreground observation should record changes once enabled"
        assert all(event["app"] != "WINWORD.EXE" for event in events)
        # Retention bound: never more than the configured window.
        assert len(observation._history) <= 3  # noqa: SLF001
    finally:
        observation.set_observation(False)
    assert observation.observation_enabled is False


def test_focus_action_is_governed_and_receipted(provider) -> None:
    resources = provider
    observation, state = _make(resources, windows=(SNAPSHOT_A,), foreground=101)
    observation.observe_and_project()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ActionLedgerStore(Path(tmp) / "ledger.db")
        service = WindowActionService(
            director=_FakeDirector(),
            provider=observation,
            resource_store=resources,
            ledger=ledger,
            clock=lambda: NOW,
        )
        result = service.request_focus(resource_id=window_resource_id(101))
        assert result == {"ok": True, "success": True, "detail": "verified in foreground"}
        assert state["focused"] == [101]

        (entry,) = ledger.list_by_household("household-authoring")
        assert entry.action == "window.focus"
        assert entry.provider_id == PROVIDER_ID
        assert entry.success is True
        assert entry.resource_id == window_resource_id(101)


def test_focus_fail_closed_on_unknown_stale_or_foreign(provider) -> None:
    resources = provider
    observation, _state = _make(resources, windows=(SNAPSHOT_A,))
    observation.observe_and_project()
    resources.save(
        type(resources.get(window_resource_id(101)))(
            resource_id="window:999",
            resource_type="window",
            scope_id="scope:personal",
            provider_id=PROVIDER_ID,
            title="ghost",
            locator=None,
            capabilities=(),
            observed_at=NOW,
            stale=True,
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        service = WindowActionService(
            director=_FakeDirector(),
            provider=observation,
            resource_store=resources,
            ledger=ActionLedgerStore(Path(tmp) / "ledger.db"),
            clock=lambda: NOW,
        )
        assert service.request_focus(resource_id="window:nope")["ok"] is False
        stale = service.request_focus(resource_id="window:999")
        assert stale["ok"] is False
        foreign = resources.get(window_resource_id(101))
        resources.save(
            type(foreign)(
                resource_id="file:notawindow",
                resource_type="file",
                scope_id="scope:personal",
                provider_id="local_filesystem",
                title="x",
                locator=None,
                capabilities=(),
                observed_at=NOW,
            )
        )
        assert service.request_focus(resource_id="file:notawindow")["ok"] is False


def test_real_machine_enumeration_finds_actual_windows() -> None:
    """Against the real win32 seam on this machine, at least one visible,
    titled top-level window exists (the test host itself lives in one)."""

    observation = WindowObservationProvider(
        resource_store=ResourceStore(Path(tempfile.mkdtemp()) / "r.db"),
        scope_id="scope:personal",
        clock=lambda: NOW,
    )
    windows = observation.enumerate_windows()
    assert isinstance(windows, tuple)
    assert len(windows) >= 1
    for snapshot in windows:
        assert snapshot.title.strip()
        assert snapshot.process_name
        assert snapshot.hwnd > 0
