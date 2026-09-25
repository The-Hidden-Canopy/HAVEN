using System.Text.Json;

namespace Haven.Desktop;

/// <summary>
/// The shell-level connection lifecycle (spec section 11).
///
/// STARTING → CONNECTING → CONNECTED / DEGRADED → RECONNECTING
/// (250ms → 500ms → 1s → 2s → 5s cap) → CONNECTED; prolonged failure →
/// DISCONNECTED. The dead pipe is disposed before reconnecting, both
/// channels re-authenticate, and the currently visible domain reconciles
/// first (background domains reconcile on next activation).
///
/// Pages keep their own per-section errors; this service owns the shell's
/// connection state, the central banner, and the shared retry affordance.
/// </summary>
public sealed class ConnectionService
{
    public const string StateStarting = "STARTING";
    public const string StateConnecting = "CONNECTING";
    public const string StateConnected = "CONNECTED";
    public const string StateDegraded = "DEGRADED";
    public const string StateReconnecting = "RECONNECTING";
    public const string StateDisconnected = "DISCONNECTED";

    /// <summary>Backoff schedule: 250ms → 500ms → 1s → 2s → 5s cap.</summary>
    public static readonly int[] BackoffMs = { 250, 500, 1000, 2000, 5000 };

    /// <summary>After this many failed attempts the shell gives up to DISCONNECTED.</summary>
    public const int MaxAttemptsBeforeDisconnected = 12;

    private readonly Func<Task<HavenCoreClient>> _connectOnce;
    private readonly Action<string> _onStateChanged;
    private readonly Func<Task> _onReconciled;
    private int _attempt;
    private volatile bool _stopping;
    private CancellationTokenSource? _backoffCts;

    public ConnectionService(
        Func<Task<HavenCoreClient>> connectOnce,
        Action<string> onStateChanged,
        Func<Task> onReconciled)
    {
        _connectOnce = connectOnce;
        _onStateChanged = onStateChanged;
        _onReconciled = onReconciled;
    }

    public string State { get; private set; } = StateStarting;

    public bool IsOnline => State is StateConnected or StateDegraded;

    public bool IsMutationsEnabled => State is StateConnected;

    /// <summary>Failed attempts since the last successful connect (0 once connected).</summary>
    public int Attempt => _attempt;

    /// <summary>The backoff delay the next reconnect attempt will wait, in ms.</summary>
    public int NextRetryDelayMs => BackoffForAttempt(Math.Max(_attempt - 1, 0));

    public static int BackoffForAttempt(int attempt)
    {
        var index = Math.Clamp(attempt, 0, BackoffMs.Length - 1);
        return BackoffMs[index];
    }

    public async Task RunAsync(CancellationToken cancellationToken = default)
    {
        SetState(StateConnecting);
        while (!_stopping && !cancellationToken.IsCancellationRequested)
        {
            try
            {
                var client = await _connectOnce();
                _attempt = 0;
                SetState(StateConnected);
                try
                {
                    await _onReconciled();
                }
                catch
                {
                    // Reconcile failures demote to DEGRADED but keep the pipe.
                    SetState(StateDegraded);
                    await WatchForFailureAsync(client, cancellationToken);
                    continue;
                }
                await WatchForFailureAsync(client, cancellationToken);
            }
            catch (Exception)
            {
                if (_stopping || cancellationToken.IsCancellationRequested)
                {
                    break;
                }
                _attempt += 1;
                if (_attempt >= MaxAttemptsBeforeDisconnected)
                {
                    SetState(StateDisconnected);
                    return;
                }
                SetState(StateReconnecting);
                _backoffCts = new CancellationTokenSource();
                try
                {
                    await Task.Delay(BackoffForAttempt(_attempt - 1), _backoffCts.Token);
                }
                catch (OperationCanceledException)
                {
                    // Retry Now short-circuits the backoff.
                }
                SetState(StateConnecting);
            }
        }
    }

    private async Task WatchForFailureAsync(HavenCoreClient client, CancellationToken cancellationToken)
    {
        // Cheap liveness probe: a state.get round-trip every 10s surfaces a
        // dead pipe without page-level errors. Any failure throws and the
        // loop above disposes, backs off, and re-authenticates.
        while (!_stopping && !cancellationToken.IsCancellationRequested)
        {
            await Task.Delay(10_000, cancellationToken);
            var state = await client.GetStateAsync(cancellationToken);
            if (state.ValueKind != JsonValueKind.Object)
            {
                throw new InvalidOperationException("HAVEN Core returned an invalid state frame.");
            }
        }
    }

    public Task RetryNowAsync()
    {
        _attempt = 0;
        // Interrupts a backoff delay mid-wait; if the loop already gave up to
        // DISCONNECTED the caller restarts RunAsync.
        _backoffCts?.Cancel();
        SetState(StateConnecting);
        return Task.CompletedTask;
    }

    public void Stop()
    {
        _stopping = true;
    }

    private void SetState(string state)
    {
        if (State == state)
        {
            return;
        }
        State = state;
        _onStateChanged(state);
    }
}
