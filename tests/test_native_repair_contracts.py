"""Source-level contracts for the native repair pass.

The repository does not include a runnable .NET/WinUI toolchain in this
environment, so these checks protect the high-risk lifecycle and Today
contracts alongside the Python integration tests.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "Haven.Desktop"


def test_event_client_suppresses_intentional_disconnect_and_exposes_stall_state():
    text = (NATIVE / "Services" / "HavenEventClient.cs").read_text(encoding="utf-8")
    assert "public bool IsStalled" in text
    assert "public DateTime? LastFrameUtc" in text
    assert "if (!cancellationToken.IsCancellationRequested)" in text
    assert "ConnectionLost?.Invoke();" in text
    assert "if (!IsConnected)" in text


def test_main_window_owns_event_reconnect_and_shutdown_lifecycle():
    events = (NATIVE / "MainWindow.Events.cs").read_text(encoding="utf-8")
    window = (NATIVE / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    connection = (NATIVE / "Services" / "ConnectionService.cs").read_text(encoding="utf-8")
    assert "private readonly SemaphoreSlim _eventLifecycleLock" in events
    assert "HeartbeatMissed" in events
    assert "RequestEventClientReconnect();" in events
    assert "await previous.DisposeAsync();" in events
    assert "_ = StopEventClientAsync();" in window
    assert "_ = StopCoreClientAsync();" in window
    assert "_stopCts.Cancel();" in connection


def test_today_uses_one_snapshot_request_and_never_renders_raw_provider_errors():
    client = (NATIVE / "Services" / "HavenCoreClient.cs").read_text(encoding="utf-8")
    today = (NATIVE / "MainWindow.Today.cs").read_text(encoding="utf-8")
    assert 'RequestResultAsync("today.snapshot"' in client
    assert "GetTodaySnapshotAsync" in today
    assert "Text = ex.Message" not in today


def test_generated_native_snapshot_and_root_scratch_scripts_are_ignored_and_absent():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "native/Haven.Desktop/bin-shot/" in ignore
    assert "tmp-*.py" in ignore
    assert not (NATIVE / "bin-shot").exists()
    assert not list(ROOT.glob("tmp-*.py"))
