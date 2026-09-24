"""IPC adapter for computer awareness: lists are read-mostly, focus is governed,
observation stays opt-in, and files come from the existing projection."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.integrations.computer.windows import WindowSnapshot, window_resource_id
from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class _FakeObservation:
    def __init__(self, resources=None, scope_id="scope:personal") -> None:
        self.enabled = False
        self.suppressed: set[str] = set()
        self.windows = [
            WindowSnapshot(hwnd=101, title="Proposal.docx - Word", process_id=10, process_name="WINWORD.EXE"),
            WindowSnapshot(hwnd=202, title="HAVEN console", process_id=20, process_name="python.exe"),
        ]
        self.focused: list[int] = []
        self._resources = resources
        self._scope_id = scope_id

    def apps(self):
        return (
            {"app": "WINWORD.EXE", "windows": 1, "titles": ["Proposal.docx - Word"]},
            {"app": "python.exe", "windows": 1, "titles": ["HAVEN console"]},
        )

    def enumerate_windows(self):
        return tuple(self.windows)

    def observe_and_project(self):
        if self._resources is not None:
            for snapshot in self.windows:
                self._resources.save(
                    __import__("haven.resources", fromlist=["ResourceRecord"]).ResourceRecord(
                        resource_id=window_resource_id(snapshot.hwnd),
                        resource_type="window",
                        scope_id=self._scope_id,
                        provider_id="haven.windows",
                        title=snapshot.title,
                        locator=None,
                        capabilities=("window.focus",),
                        observed_at=NOW,
                        metadata=(
                            ("process", snapshot.process_name),
                            ("hwnd", str(snapshot.hwnd)),
                        ),
                    )
                )
        return tuple(self.windows)

    def status(self):
        return {
            "enabled": self.enabled,
            "suppressed_apps": sorted(self.suppressed),
            "retention": 200,
            "observed_events": 0,
        }

    def activity(self, *, limit: int = 50):
        return ()

    def set_observation(self, enabled: bool):
        self.enabled = enabled
        return {"ok": True, "observation": self.status()}

    def set_suppressed(self, app: str, suppressed: bool):
        if not app.strip():
            return {"ok": False, "error": "a non-empty 'app' is required"}
        (self.suppressed.add if suppressed else self.suppressed.discard)(app.strip().casefold())
        return {"ok": True, "observation": self.status()}

    def bring_window_to_foreground(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return True

    def _foreground_hwnd(self):
        return self.focused[-1] if self.focused else 0


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        fake = _FakeObservation(
            resources=instance.resources, scope_id=instance.identity.personal_scope_id
        )
        instance.windows_provider = fake
        instance.window_actions.set_provider(fake)
        try:
            yield instance, fake
        finally:
            instance.server_close()


def _dispatch(instance, method: str, params: dict) -> dict:
    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def test_apps_windows_and_files_lists(server) -> None:
    instance, _fake = server
    apps = _dispatch(instance, "computer.apps.list", {})
    assert apps["result"]["ok"] is True
    assert {row["app"] for row in apps["result"]["apps"]} == {"WINWORD.EXE", "python.exe"}

    windows = _dispatch(instance, "computer.windows.list", {})
    assert [row["title"] for row in windows["result"]["windows"]] == [
        "Proposal.docx - Word",
        "HAVEN console",
    ]
    assert windows["result"]["windows"][0]["resource_id"] == "window:101"

    instance.resources.save(
        __import__("haven.resources", fromlist=["ResourceRecord"]).ResourceRecord(
            resource_id="file:seen.txt",
            resource_type="file",
            scope_id=instance.identity.personal_scope_id,
            provider_id="local_filesystem",
            title="seen.txt",
            locator="E:/roots/seen.txt",
            capabilities=(),
            observed_at=NOW,
        )
    )
    files = _dispatch(instance, "computer.files.list", {})
    rows = {row["resource_id"]: row for row in files["result"]["files"]}
    assert rows["file:seen.txt"]["title"] == "seen.txt"
    assert "window:101" not in rows


def test_window_focus_is_governed_and_receipted(server) -> None:
    instance, fake = server
    _dispatch(instance, "computer.windows.list", {})
    # The demo director has no declared owner: fail closed.
    denied = _dispatch(instance, "computer.window.focus", {"resource_id": "window:101"})
    assert denied["result"]["ok"] is False

    instance.setup.declare_person(name="Gerron Smith", role="owner")
    focused = _dispatch(instance, "computer.window.focus", {"resource_id": "window:101"})
    assert focused["result"]["ok"] is True
    assert focused["result"]["success"] is True
    assert fake.focused == [101]

    ghost = _dispatch(instance, "computer.window.focus", {"resource_id": "window:404"})
    assert ghost["result"]["ok"] is False

    history = _dispatch(instance, "computer.action.history", {})
    entries = [row for row in history["result"]["entries"] if row["action"] == "window.focus"]
    # Validation refusals (unknown window) fail before authority, like the
    # filesystem service; the executed request carries the receipt.
    assert len(entries) == 1
    assert entries[0]["resource_id"] == "window:101"
    assert entries[0]["success"] is True


def test_observation_is_opt_in_and_suppressible(server) -> None:
    instance, fake = server
    status = _dispatch(instance, "computer.activity.list", {})
    assert status["result"]["observation"]["enabled"] is False
    assert status["result"]["events"] == []

    bad = _dispatch(instance, "computer.observation.set", {"enabled": "yes"})
    assert bad["ok"] is False

    enabled = _dispatch(instance, "computer.observation.set", {"enabled": True})
    assert enabled["result"]["observation"]["enabled"] is True

    suppressed = _dispatch(
        instance, "computer.observation.suppress", {"app": "WINWORD.EXE", "suppressed": True}
    )
    assert suppressed["result"]["observation"]["suppressed_apps"] == ["winword.exe"]

    blank = _dispatch(instance, "computer.observation.suppress", {"app": " "})
    assert blank["result"]["ok"] is False
