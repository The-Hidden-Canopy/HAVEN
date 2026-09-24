"""Application/window awareness: observation only, never control-by-default.

Spec page 30: top-level window enumeration + process identity via user32/
kernel32, foreground-change observation that is opt-in with a per-app
suppression list, bounded retention. No keylog, no screenshots, no
UIAutomation. The single write-side action is bringing a window to the
foreground -- low-risk, and it still crosses the governed, receipted path
(`haven/web/window_actions.py`), never this provider directly.

Every win32 seam is injectable so tests run hermetically against fake
windows; the defaults are thin ctypes wrappers.
"""

from __future__ import annotations

import ctypes
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

PROVIDER_ID = "haven.windows"

_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None


@dataclass(frozen=True)
class WindowSnapshot:
    hwnd: int
    title: str
    process_id: int
    process_name: str


@dataclass(frozen=True)
class ForegroundEvent:
    app: str
    title: str
    at: datetime


def _default_process_name(process_id: int) -> str:
    if kernel32 is None:
        return f"pid:{process_id}"
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return f"pid:{process_id}"
    try:
        buffer = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(512)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rpartition("\\")[2] or buffer.value
        return f"pid:{process_id}"
    finally:
        kernel32.CloseHandle(handle)


def _default_enum_windows() -> tuple[WindowSnapshot, ...]:
    """Enumerate visible, titled, non-tool top-level windows."""

    if user32 is None or kernel32 is None:
        return ()
    snapshots: list[WindowSnapshot] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, _GW_OWNER):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        snapshots.append(
            WindowSnapshot(
                hwnd=int(hwnd),
                title=title,
                process_id=int(process_id.value),
                process_name=_default_process_name(int(process_id.value)),
            )
        )
        return True

    user32.EnumWindows(callback_type(callback), None)
    return tuple(snapshots)


def _default_foreground_hwnd() -> int:
    if user32 is None:
        return 0
    return int(user32.GetForegroundWindow())


def _default_bring_to_foreground(hwnd: int) -> bool:
    """Show + focus. Best-effort: Windows may refuse foreground steals."""

    if user32 is None:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    return bool(user32.SetForegroundWindow(hwnd))


def window_resource_id(hwnd: int) -> str:
    return f"window:{hwnd}"


class WindowObservationProvider:
    """Enumerates, projects, and (opt-in) watches foreground changes.

    `resource_store` rows are the projection: `resource_type="window"` and
    `resource_type="application"` records in the provider's scope, stale
    the moment a window no longer exists.
    """

    def __init__(
        self,
        *,
        resource_store: ResourceStore,
        scope_id: str,
        clock=_DEFAULT_CLOCK,
        retention: int = 200,
        enum_windows: Callable[[], tuple[WindowSnapshot, ...]] = _default_enum_windows,
        foreground_hwnd: Callable[[], int] = _default_foreground_hwnd,
        bring_to_foreground: Callable[[int], bool] = _default_bring_to_foreground,
        poll_seconds: float = 2.0,
    ) -> None:
        self._resources = resource_store
        self._scope_id = scope_id
        self._clock = clock
        self._enum_windows = enum_windows
        self._foreground_hwnd = foreground_hwnd
        self._bring_to_foreground = bring_to_foreground
        self._poll_seconds = poll_seconds
        self._retention = retention
        self._history: deque[ForegroundEvent] = deque(maxlen=retention)
        self._suppressed: set[str] = set()
        self._observation_enabled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @property
    def observation_enabled(self) -> bool:
        return self._observation_enabled

    @property
    def suppressed_apps(self) -> tuple[str, ...]:
        return tuple(sorted(self._suppressed))

    # -- observation control ---------------------------------------------------

    def set_observation(self, enabled: bool) -> dict:
        with self._lock:
            if enabled and not self._observation_enabled:
                self._observation_enabled = True
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._watch_foreground,
                    daemon=True,
                    name="haven-foreground-observation",
                )
                self._thread.start()
            elif not enabled and self._observation_enabled:
                self._observation_enabled = False
                self._stop.set()
                thread = self._thread
                self._thread = None
                if thread is not None:
                    thread.join(timeout=5)
        return {"ok": True, "observation": self.status()}

    def set_suppressed(self, app: str, suppressed: bool) -> dict:
        if not isinstance(app, str) or not app.strip():
            return {"ok": False, "error": "a non-empty 'app' is required"}
        name = app.strip().casefold()
        with self._lock:
            if suppressed:
                self._suppressed.add(name)
            else:
                self._suppressed.discard(name)
        return {"ok": True, "observation": self.status()}

    def status(self) -> dict:
        return {
            "enabled": self._observation_enabled,
            "suppressed_apps": list(self.suppressed_apps),
            "retention": self._retention,
            "observed_events": len(self._history),
        }

    # -- reads -------------------------------------------------------------------

    def enumerate_windows(self) -> tuple[WindowSnapshot, ...]:
        return self._enum_windows()

    def apps(self) -> tuple[dict, ...]:
        """Distinct applications across live windows and recent history."""

        windows = self.enumerate_windows()
        by_app: dict[str, dict] = {}
        for snapshot in windows:
            row = by_app.setdefault(
                snapshot.process_name,
                {"app": snapshot.process_name, "windows": 0, "titles": []},
            )
            row["windows"] += 1
            row["titles"].append(snapshot.title)
        for event in self._history:
            row = by_app.setdefault(
                event.app, {"app": event.app, "windows": 0, "titles": []}
            )
            row["last_seen_at"] = event.at.isoformat()
        return tuple(sorted(by_app.values(), key=lambda row: row["app"].casefold()))

    def activity(self, *, limit: int = 50) -> tuple[dict, ...]:
        with self._lock:
            events = tuple(self._history)[-limit:]
        return tuple(
            {"app": event.app, "title": event.title, "at": event.at.isoformat()}
            for event in reversed(events)
        )

    # -- projection ---------------------------------------------------------------

    def observe_and_project(self) -> tuple[WindowSnapshot, ...]:
        """Enumerate and fold the current window set into the ResourceStore."""

        now = self._clock()
        windows = self.enumerate_windows()
        seen_ids = set()
        for snapshot in windows:
            resource_id = window_resource_id(snapshot.hwnd)
            seen_ids.add(resource_id)
            self._resources.save(
                ResourceRecord(
                    resource_id=resource_id,
                    resource_type="window",
                    scope_id=self._scope_id,
                    provider_id=PROVIDER_ID,
                    title=snapshot.title,
                    locator=None,
                    capabilities=("window.focus",),
                    observed_at=now,
                    metadata=(
                        ("process", snapshot.process_name),
                        ("hwnd", str(snapshot.hwnd)),
                    ),
                )
            )
        # A closed window is stale evidence, not deleted history.
        for record in self._resources.list_by_scope(self._scope_id):
            if record.provider_id != PROVIDER_ID or record.resource_type != "window":
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
                        metadata=record.metadata,
                        stale=True,
                    )
                )
        return windows

    # -- the single write-side capability ------------------------------------------

    def bring_window_to_foreground(self, hwnd: int) -> bool:
        """Low-risk focus action; governance lives outside this provider."""

        return self._bring_to_foreground(hwnd)

    # -- the observation thread ------------------------------------------------------

    def _watch_foreground(self) -> None:
        last_hwnd = 0
        while not self._stop.wait(self._poll_seconds):
            try:
                hwnd = self._foreground_hwnd()
            except Exception:
                continue
            if hwnd == 0 or hwnd == last_hwnd:
                continue
            last_hwnd = hwnd
            snapshot = next(
                (item for item in self.enumerate_windows() if item.hwnd == hwnd), None
            )
            if snapshot is None or snapshot.process_name.casefold() in self._suppressed:
                continue
            with self._lock:
                self._history.append(
                    ForegroundEvent(
                        app=snapshot.process_name,
                        title=snapshot.title,
                        at=self._clock(),
                    )
                )

    def close(self) -> None:
        self.set_observation(False)


__all__ = [
    "ForegroundEvent",
    "PROVIDER_ID",
    "WindowObservationProvider",
    "WindowSnapshot",
    "window_resource_id",
]
