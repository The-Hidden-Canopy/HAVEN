"""Source-level contracts for the native repair pass.

The repository does not include a runnable .NET/WinUI toolchain in every
environment, so these checks protect the high-risk lifecycle and Today
contracts alongside the Python integration tests even where `dotnet` is
unavailable. Where it *is* available, `native/Haven.Desktop.Tests` (xunit)
now additionally exercises the reconnect/stall/shutdown behavior these
checks only grep for -- real named pipes, real timing, real assertions
against `EventClientCoordinator` -- so these stay as a fast, always-on
floor rather than the only net.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "Haven.Desktop"
CORE_CLIENT = ROOT / "native" / "Haven.Core.Client"


def test_event_client_suppresses_intentional_disconnect_and_exposes_stall_state():
    text = (CORE_CLIENT / "HavenEventClient.cs").read_text(encoding="utf-8")
    assert "public bool IsStalled" in text
    assert "public DateTime? LastFrameUtc" in text
    assert "if (!cancellationToken.IsCancellationRequested)" in text
    assert "ConnectionLost?.Invoke();" in text
    assert "if (!IsConnected)" in text


def test_event_reconnect_and_shutdown_lifecycle_is_owned_by_the_coordinator():
    # Pipe-lifecycle coordination (lock, backoff, dispose-and-replace) was
    # extracted out of MainWindow.Events.cs into EventClientCoordinator
    # (native/Haven.Core.Client) specifically so it could be unit-tested
    # without a WinUI host -- see native/Haven.Desktop.Tests.
    coordinator = (CORE_CLIENT / "EventClientCoordinator.cs").read_text(encoding="utf-8")
    events = (NATIVE / "MainWindow.Events.cs").read_text(encoding="utf-8")
    window = (NATIVE / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    connection = (NATIVE / "Services" / "ConnectionService.cs").read_text(encoding="utf-8")
    assert "private readonly SemaphoreSlim _lifecycleLock" in coordinator
    assert "await previous.DisposeAsync();" in coordinator
    assert "HeartbeatMissed" in events
    assert "RequestEventClientReconnect();" in events
    assert "_ = StopEventClientAsync();" in window
    assert "_ = StopCoreClientAsync();" in window
    assert "_stopCts.Cancel();" in connection


def test_today_uses_one_snapshot_request_and_never_renders_raw_provider_errors():
    client = (NATIVE / "Services" / "HavenCoreClient.cs").read_text(encoding="utf-8")
    today = (NATIVE / "MainWindow.Today.cs").read_text(encoding="utf-8")
    assert 'RequestResultAsync("today.snapshot"' in client
    assert "GetTodaySnapshotAsync" in today
    assert "Text = ex.Message" not in today


def test_today_keeps_unavailable_sections_distinct_from_empty_sections():
    today = (NATIVE / "MainWindow.Today.cs").read_text(encoding="utf-8")
    assert "ok.ValueKind == JsonValueKind.False" in today
    assert "This section is temporarily unavailable." in today
    assert "Focus is temporarily unavailable." in today
    assert "Upcoming is temporarily unavailable." in today
    assert "Needs You is temporarily unavailable." in today
    assert "card.Visibility = Visibility.Collapsed" in today
    assert "return true; // an error is shown, not silently treated as \"empty\"." in today


def test_connection_center_reports_rpc_and_event_health_as_separate_channels():
    connection = (NATIVE / "MainWindow.ConnectionCenter.cs").read_text(encoding="utf-8")
    assert '"HAVEN Core (RPC)"' in connection
    assert '"Live updates (events)"' in connection
    assert '"Stalled (last frame ' in connection
    assert '"Reconnecting (attempt ' in connection


def test_discover_surfaces_transport_readiness_instead_of_claiming_bluetooth_scan():
    client = (NATIVE / "Services" / "HavenCoreClient.cs").read_text(encoding="utf-8")
    discover = (NATIVE / "MainWindow.Discover.cs").read_text(encoding="utf-8")
    assert 'RequestResultAsync("discovery.scan"' in client
    assert '"transports"' in discover
    assert "Scanning configured discovery transports" in discover
    assert "Transport readiness is unavailable from this Core version." in discover
    assert "Scanning nearby WiFi (SSDP + mDNS) and Bluetooth" not in discover


def test_native_email_delete_stays_confirmation_gated_and_provider_scoped():
    client = (NATIVE / "Services" / "HavenCoreClient.cs").read_text(encoding="utf-8")
    comms = (NATIVE / "MainWindow.Comms.cs").read_text(encoding="utf-8")
    assert 'RequestResultAsync("email.message.delete"' in client
    assert 'RequestResultAsync("email.message.confirm"' in client
    assert 'RequestResultAsync("email.message.deny"' in client
    assert "Delete from mailbox" in comms
    assert "confirmation_required" in comms
    assert "_emailCanMutate" in comms


def test_native_activity_renders_recent_work_evidence():
    activity = (NATIVE / "MainWindow.Computer.cs").read_text(encoding="utf-8")
    client = (NATIVE / "Services" / "HavenCoreClient.cs").read_text(encoding="utf-8")
    assert '"recent_work"' in activity
    assert "GetComputerActivityAsync" in client


def test_native_settings_projects_the_support_safe_unified_receipt_export():
    system = (NATIVE / "MainWindow.System.cs").read_text(encoding="utf-8")
    xaml = (NATIVE / "MainWindow.xaml").read_text(encoding="utf-8")
    assert "ExportDiagnosticsAsync" in system
    assert "RenderUnifiedReceipts" in system
    assert '"unified_receipts"' in system
    assert "ReceiptsList" in xaml
    assert "Recent governed actions" in xaml


def test_generated_native_snapshot_and_root_scratch_scripts_are_ignored_and_absent():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "native/Haven.Desktop/bin-shot/" in ignore
    assert "tmp-*.py" in ignore
    assert not (NATIVE / "bin-shot").exists()
    assert not list(ROOT.glob("tmp-*.py"))
