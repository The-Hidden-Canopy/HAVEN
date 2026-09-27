namespace Haven.Desktop;

/// <summary>
/// Lifecycle coordination for <see cref="HavenEventClient"/>: one reconnect
/// attempt in flight at a time, exponential backoff, stall detection driving
/// reconnect, and a clean shutdown that never races a reconnect in progress.
///
/// This used to live inline in <c>MainWindow.Events.cs</c> as private fields
/// and methods (the native product-consolidation plan's own reading of the
/// repo: "several native surfaces are build-verified and contract-tested but
/// not interactively clicked end-to-end" -- extracting the coordination out
/// of the WinUI partial class is what makes it independently, deterministically
/// testable without a UI host). <see cref="MainWindow.Events.cs"/> now owns
/// only presentation concerns (which domains are dirty, which page is
/// visible) and delegates every pipe-lifecycle decision here.
/// </summary>
public sealed class EventClientCoordinator : IAsyncDisposable
{
    private readonly Func<string, string, HavenEventClient> _clientFactory;
    private readonly Action<string, System.Text.Json.JsonElement> _onEvent;
    private readonly Action<EventClientCoordinator> _onHeartbeatMissed;
    private readonly Action<EventClientCoordinator> _onConnectionLost;

    private readonly SemaphoreSlim _lifecycleLock = new(1, 1);
    private readonly CancellationTokenSource _lifecycleCts = new();
    private HavenEventClient? _client;
    private Task? _reconnectTask;
    private bool _stopping;

    // Exposed for tests -- production callers should use the defaults, but a
    // deterministic test cannot wait out a real 2s/10s backoff.
    public TimeSpan InitialRetryDelay { get; init; } = TimeSpan.FromSeconds(2);
    public TimeSpan MaxRetryDelay { get; init; } = TimeSpan.FromSeconds(10);
    public TimeSpan ConnectionLostGrace { get; init; } = TimeSpan.FromSeconds(2);

    public EventClientCoordinator(
        Func<string, string, HavenEventClient> clientFactory,
        Action<string, System.Text.Json.JsonElement> onEvent,
        Action<EventClientCoordinator> onHeartbeatMissed,
        Action<EventClientCoordinator> onConnectionLost)
    {
        _clientFactory = clientFactory;
        _onEvent = onEvent;
        _onHeartbeatMissed = onHeartbeatMissed;
        _onConnectionLost = onConnectionLost;
    }

    /// <summary>The live client, if a reconnect has completed. Never assume
    /// this is connected -- check <see cref="IsConnectedAndFresh"/>.</summary>
    public HavenEventClient? Client => _client;

    public bool IsConnectedAndFresh => _client is { IsConnected: true, IsStalled: false };

    /// <summary>True whenever a reconnect attempt is currently running.
    /// Exposed so a test can assert "only one attempt is ever in flight"
    /// without reaching into private state.</summary>
    public bool ReconnectInProgress => _reconnectTask is { IsCompleted: false };

    public int ReconnectAttempts { get; private set; }

    /// <summary>Ensure a fresh connection exists, starting one reconnect
    /// attempt if needed. A no-op if already connected or already
    /// reconnecting -- concurrent callers collapse to the one in-flight
    /// attempt (the plan's "concurrent reconnect requests" case).</summary>
    public void EnsureConnected(string pipeName, string authToken, Func<bool> isOnline)
    {
        if (_stopping || !isOnline())
        {
            return;
        }
        if (IsConnectedAndFresh)
        {
            return;
        }
        RequestReconnect(pipeName, authToken, isOnline);
    }

    public void RequestReconnect(string pipeName, string authToken, Func<bool> isOnline)
    {
        if (_stopping || !isOnline())
        {
            return;
        }
        if (ReconnectInProgress)
        {
            return;
        }
        _reconnectTask = ReconnectAsync(pipeName, authToken, isOnline);
    }

    private async Task ReconnectAsync(string pipeName, string authToken, Func<bool> isOnline)
    {
        var retryDelay = InitialRetryDelay;
        while (!_stopping && isOnline())
        {
            try
            {
                await _lifecycleLock.WaitAsync(_lifecycleCts.Token);
                try
                {
                    if (_stopping || !isOnline())
                    {
                        return;
                    }
                    if (IsConnectedAndFresh)
                    {
                        return;
                    }

                    var previous = _client;
                    _client = null;
                    if (previous is not null)
                    {
                        await previous.DisposeAsync();
                    }

                    ReconnectAttempts++;
                    var client = _clientFactory(pipeName, authToken);
                    client.EventReceived = (eventName, payload) => _onEvent(eventName, payload);
                    client.HeartbeatMissed = () => OnHeartbeatMissed(client);
                    client.ConnectionLost = () => _ = OnConnectionLostAsync(client, isOnline);
                    _client = client;
                    try
                    {
                        await client.ConnectAsync(_lifecycleCts.Token);
                        return;
                    }
                    catch (Exception)
                    {
                        if (ReferenceEquals(_client, client))
                        {
                            _client = null;
                        }
                        await client.DisposeAsync();
                    }
                }
                finally
                {
                    _lifecycleLock.Release();
                }
            }
            catch (OperationCanceledException) when (_lifecycleCts.IsCancellationRequested)
            {
                return;
            }
            catch (Exception)
            {
                // A failed events channel must not take down the caller.
            }

            try
            {
                await Task.Delay(retryDelay, _lifecycleCts.Token);
            }
            catch (OperationCanceledException) when (_lifecycleCts.IsCancellationRequested)
            {
                return;
            }
            retryDelay = TimeSpan.FromMilliseconds(Math.Min(retryDelay.TotalMilliseconds * 2, MaxRetryDelay.TotalMilliseconds));
        }
    }

    private void OnHeartbeatMissed(HavenEventClient source)
    {
        if (!ReferenceEquals(_client, source) || _stopping)
        {
            return;
        }
        _onHeartbeatMissed(this);
    }

    private async Task OnConnectionLostAsync(HavenEventClient source, Func<bool> isOnline)
    {
        if (!ReferenceEquals(_client, source) || _stopping || !isOnline())
        {
            return;
        }
        try
        {
            await Task.Delay(ConnectionLostGrace, _lifecycleCts.Token);
        }
        catch (OperationCanceledException) when (_lifecycleCts.IsCancellationRequested)
        {
            return;
        }
        if (!ReferenceEquals(_client, source) || _stopping || !isOnline())
        {
            return;
        }
        _onConnectionLost(this);
    }

    /// <summary>Cancels any in-flight reconnect, waits for it to unwind, then
    /// tears down the live client. Safe to call while a reconnect is
    /// running -- the plan's "shutdown during reconnect" case.</summary>
    public async Task StopAsync()
    {
        if (_stopping)
        {
            return;
        }
        _stopping = true;
        _lifecycleCts.Cancel();
        try
        {
            if (_reconnectTask is not null)
            {
                await _reconnectTask;
            }
        }
        catch (Exception)
        {
            // Shutdown is best-effort; the process is exiting.
        }
        await _lifecycleLock.WaitAsync();
        try
        {
            var client = _client;
            _client = null;
            if (client is not null)
            {
                await client.DisposeAsync();
            }
        }
        finally
        {
            _lifecycleLock.Release();
        }
    }

    public async ValueTask DisposeAsync() => await StopAsync();
}
