using Xunit;

namespace Haven.Desktop.Tests;

/// <summary>Deterministic tests for the pipe-lifecycle coordination that used
/// to live inline in `MainWindow.Events.cs` (native product-consolidation
/// plan, P0: "deterministic tests for concurrent reconnect requests, stall
/// callback, shutdown during reconnect and token/pipe replacement"). Every
/// test drives a real `HavenEventClient` against a real local named pipe
/// (`FakeCoreServer`) rather than a mock, the same "prove it against the
/// actual mechanism" standard `test_bluetooth_fixture_backend.py` already
/// applies on the Python side.</summary>
public class EventClientCoordinatorTests
{
    private static string NewRpcPipeName() => "haven-test-" + Guid.NewGuid().ToString("N");

    private static async Task WaitUntilAsync(Func<bool> condition, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            if (condition())
            {
                return;
            }
            await Task.Delay(10);
        }
        Assert.True(condition(), "condition was not met within the timeout");
    }

    [Fact]
    public async Task Concurrent_reconnect_requests_collapse_to_one_attempt()
    {
        var rpcPipe = NewRpcPipeName();
        var eventsPipe = HavenEventClient.DerivePipeName(rpcPipe);
        var serverTask = FakeCoreServer.AcceptAndAuthenticateAsync(eventsPipe);

        await using var coordinator = new EventClientCoordinator(
            (pipe, token) => new HavenEventClient(pipe, token),
            onEvent: (_, _) => { },
            onHeartbeatMissed: _ => { },
            onConnectionLost: _ => { });

        // Two callers racing to ensure a connection -- e.g. a heartbeat-missed
        // callback and a page navigation firing at the same time -- must
        // collapse to the one in-flight attempt, not open two pipes.
        coordinator.RequestReconnect(rpcPipe, "token-a", () => true);
        coordinator.RequestReconnect(rpcPipe, "token-a", () => true);

        await using var server = await serverTask.WaitAsync(TimeSpan.FromSeconds(5));
        await WaitUntilAsync(() => coordinator.IsConnectedAndFresh, TimeSpan.FromSeconds(5));

        Assert.Equal(1, coordinator.ReconnectAttempts);
    }

    [Fact]
    public async Task Stall_callback_triggers_a_second_connect_attempt()
    {
        var rpcPipe = NewRpcPipeName();
        var eventsPipe = HavenEventClient.DerivePipeName(rpcPipe);
        var firstServerTask = FakeCoreServer.AcceptAndAuthenticateAsync(eventsPipe);

        // Fast-but-real watchdog timing: the actual `WatchdogTick()`/
        // `_stalledNotified` mechanism runs, just on milliseconds instead of
        // the production 5s/30s pair, so this proves the real stall-
        // detection path rather than a hand-simulated one.
        await using var coordinator = new EventClientCoordinator(
            (pipe, token) => new HavenEventClient(
                pipe, token, watchdogInterval: TimeSpan.FromMilliseconds(30), stalledThreshold: TimeSpan.FromMilliseconds(100)),
            onEvent: (_, _) => { },
            onHeartbeatMissed: c => c.RequestReconnect(rpcPipe, "token-a", () => true),
            onConnectionLost: _ => { });

        coordinator.RequestReconnect(rpcPipe, "token-a", () => true);
        var firstServer = await firstServerTask.WaitAsync(TimeSpan.FromSeconds(5));
        await WaitUntilAsync(() => coordinator.IsConnectedAndFresh, TimeSpan.FromSeconds(5));
        Assert.Equal(1, coordinator.ReconnectAttempts);
        var firstClient = coordinator.Client;

        // Say nothing on the first connection -- past `stalledThreshold`,
        // the real watchdog inside HavenEventClient fires HeartbeatMissed
        // on its own; the fake server allows unlimited instances, so the
        // second connect attempt doesn't have to wait for the first one to
        // actually close (a stall is silence, not necessarily a dead pipe).
        var secondServerTask = FakeCoreServer.AcceptAndAuthenticateAsync(eventsPipe);

        await using var secondServer = await secondServerTask.WaitAsync(TimeSpan.FromSeconds(5));
        await WaitUntilAsync(() => coordinator.ReconnectAttempts == 2, TimeSpan.FromSeconds(5));
        Assert.NotSame(firstClient, coordinator.Client);
        await firstServer.DisposeAsync();
    }

    [Fact]
    public async Task Shutdown_during_an_in_flight_reconnect_completes_cleanly()
    {
        // No server is ever started for this pipe name -- ConnectAsync will
        // sit in its connect-or-retry loop, exactly the state a real
        // "core process died mid-reconnect" shutdown would need to unwind.
        var rpcPipe = NewRpcPipeName();
        var coordinator = new EventClientCoordinator(
            (pipe, token) => new HavenEventClient(pipe, token),
            onEvent: (_, _) => { },
            onHeartbeatMissed: _ => { },
            onConnectionLost: _ => { });

        coordinator.RequestReconnect(rpcPipe, "token-a", () => true);
        await Task.Delay(50); // let the reconnect loop actually start waiting on the pipe

        var stop = coordinator.StopAsync();
        var completed = await Task.WhenAny(stop, Task.Delay(TimeSpan.FromSeconds(10)));
        Assert.Same(stop, completed);
        await stop; // rethrows if StopAsync itself faulted

        Assert.False(coordinator.ReconnectInProgress);
        Assert.Null(coordinator.Client);
    }

    [Fact]
    public async Task Reconnecting_with_a_new_pipe_and_token_replaces_the_old_client()
    {
        // A data-dir change / installation rebuild rotates the RPC pipe name
        // and auth token (ConnectionService) between connects. The
        // coordinator takes both per call rather than fixing them at
        // construction specifically so a reconnect triggered after such a
        // rotation lands on the new transport, not the old one.
        var rpcPipeA = NewRpcPipeName();
        var eventsPipeA = HavenEventClient.DerivePipeName(rpcPipeA);
        var rpcPipeB = NewRpcPipeName();
        var eventsPipeB = HavenEventClient.DerivePipeName(rpcPipeB);
        var serverATask = FakeCoreServer.AcceptAndAuthenticateAsync(eventsPipeA);

        await using var coordinator = new EventClientCoordinator(
            (pipe, token) => new HavenEventClient(
                pipe, token, watchdogInterval: TimeSpan.FromMilliseconds(30), stalledThreshold: TimeSpan.FromMilliseconds(100)),
            onEvent: (_, _) => { },
            onHeartbeatMissed: c => c.RequestReconnect(rpcPipeB, "token-b", () => true),
            onConnectionLost: _ => { });

        coordinator.RequestReconnect(rpcPipeA, "token-a", () => true);
        await using var serverA = await serverATask.WaitAsync(TimeSpan.FromSeconds(5));
        await WaitUntilAsync(() => coordinator.IsConnectedAndFresh, TimeSpan.FromSeconds(5));
        var clientA = coordinator.Client;
        Assert.NotNull(clientA);
        Assert.Equal(1, coordinator.ReconnectAttempts);

        // Say nothing on A; once the (fast, injected) watchdog notices the
        // stall it asks for pipe B, not pipe A -- proving the reconnect
        // path actually uses whatever pipe/token it's given at call time,
        // not whatever it last connected with.
        var serverBTask = FakeCoreServer.AcceptAndAuthenticateAsync(eventsPipeB);
        await using var serverB = await serverBTask.WaitAsync(TimeSpan.FromSeconds(5));
        await WaitUntilAsync(() => coordinator.IsConnectedAndFresh, TimeSpan.FromSeconds(5));

        Assert.NotSame(clientA, coordinator.Client);
        Assert.Equal(2, coordinator.ReconnectAttempts);
    }
}
