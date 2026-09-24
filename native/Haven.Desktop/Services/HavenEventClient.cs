using System.Buffers.Binary;
using System.IO.Pipes;
using System.Text.Json;

namespace Haven.Desktop;

/// <summary>
/// Client for the push-only events pipe (spec sections 15-17).
///
/// The RPC pipe name (<c>haven-&lt;installation-id&gt;</c>) derives the
/// events pipe name (<c>haven-events-&lt;installation-id&gt;</c>).  After the
/// same <c>host.authenticate</c> handshake the connection is read-only: the
/// core pushes domain-invalidation frames, and a heartbeat every 15 s keeps
/// the pipe honest.  A watchdog surfaces a silently stalled pipe after 30 s
/// without any frame.
/// </summary>
public sealed class HavenEventClient : IAsyncDisposable
{
    private const string ProtocolVersion = "haven-ipc-1";
    private const int MaxFrameBytes = 1 << 20;
    private const string PipePrefix = "\\\\.\\pipe\\";
    private const string RpcPrefix = "haven-";
    private const string EventsPrefix = "haven-events-";
    private static readonly TimeSpan WatchdogInterval = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan StalledThreshold = TimeSpan.FromSeconds(30);

    private readonly string _pipeName;
    private readonly string _authToken;
    private readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };
    private NamedPipeClientStream? _pipe;
    private CancellationTokenSource? _readLoopCts;
    private Task? _readLoop;
    private Timer? _watchdog;
    private long _lastFrameUtcTicks;
    private int _stalledNotified;

    public HavenEventClient(string rpcPipeName, string authToken)
    {
        _pipeName = DerivePipeName(rpcPipeName);
        _authToken = authToken;
    }

    public static string DerivePipeName(string rpcPipeName)
    {
        var name = rpcPipeName.StartsWith(PipePrefix, StringComparison.OrdinalIgnoreCase)
            ? rpcPipeName[PipePrefix.Length..]
            : rpcPipeName;
        if (!name.StartsWith(RpcPrefix, StringComparison.Ordinal))
        {
            throw new ArgumentException("The RPC pipe name must use the haven- prefix.", nameof(rpcPipeName));
        }
        return EventsPrefix + name[RpcPrefix.Length..];
    }

    /// <summary>Raised on a background thread for every non-heartbeat event.</summary>
    public Action<string, JsonElement>? EventReceived { get; set; }

    /// <summary>Raised when no frame arrived for more than 30 seconds.</summary>
    public Action? HeartbeatMissed { get; set; }

    /// <summary>Raised once when the read loop ends (pipe broken or core shutdown).</summary>
    public Action? ConnectionLost { get; set; }

    public bool IsConnected => _pipe is { IsConnected: true };

    public async Task ConnectAsync(CancellationToken cancellationToken = default)
    {
        if (_pipe is { IsConnected: true })
        {
            return;
        }
        await DisposePipeAsync();
        var pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        await pipe.ConnectAsync(5000, cancellationToken);
        _pipe = pipe;
        Interlocked.Exchange(ref _lastFrameUtcTicks, DateTime.UtcNow.Ticks);
        Interlocked.Exchange(ref _stalledNotified, 0);

        var requestId = Guid.NewGuid().ToString("N");
        var request = JsonSerializer.SerializeToUtf8Bytes(
            new
            {
                version = ProtocolVersion,
                kind = "request",
                request_id = requestId,
                method = "host.authenticate",
                @params = new { token = _authToken },
            },
            _json);
        var frame = new byte[4 + request.Length];
        BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(0, 4), (uint)request.Length);
        request.CopyTo(frame.AsSpan(4));
        await pipe.WriteAsync(frame, cancellationToken);
        await pipe.FlushAsync(cancellationToken);
        using (var authResponse = await ReadDocumentAsync(cancellationToken))
        {
            var root = authResponse.RootElement;
            if (!root.TryGetProperty("ok", out var ok) || !ok.GetBoolean())
            {
                var error = root.TryGetProperty("error", out var errorValue)
                    ? errorValue.GetString()
                    : "HAVEN Core rejected the events-pipe authentication.";
                throw new InvalidOperationException(error);
            }
        }

        _readLoopCts = new CancellationTokenSource();
        _readLoop = Task.Run(() => ReadLoopAsync(_readLoopCts.Token));
        _watchdog ??= new Timer(_ => WatchdogTick(), null, WatchdogInterval, WatchdogInterval);
    }

    private async Task ReadLoopAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                using var document = await ReadDocumentAsync(cancellationToken);
                Interlocked.Exchange(ref _lastFrameUtcTicks, DateTime.UtcNow.Ticks);
                Interlocked.Exchange(ref _stalledNotified, 0);
                var root = document.RootElement;
                if (!root.TryGetProperty("kind", out var kind) || kind.GetString() != "event")
                {
                    continue;
                }
                var eventName = root.TryGetProperty("event", out var eventValue)
                    ? eventValue.GetString() ?? ""
                    : "";
                if (eventName == "heartbeat")
                {
                    continue;
                }
                var payload = root.TryGetProperty("payload", out var payloadValue)
                    ? payloadValue.Clone()
                    : default;
                EventReceived?.Invoke(eventName, payload);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            // An intentional disconnect.
        }
        catch (Exception)
        {
            // A broken pipe surfaces through the connection service's liveness
            // probe on the RPC channel; here we only signal the event channel.
        }
        finally
        {
            ConnectionLost?.Invoke();
        }
    }

    private void WatchdogTick()
    {
        var last = Interlocked.Read(ref _lastFrameUtcTicks);
        if (last == 0 || DateTime.UtcNow.Ticks - last < StalledThreshold.Ticks)
        {
            return;
        }
        if (Interlocked.Exchange(ref _stalledNotified, 1) == 0)
        {
            HeartbeatMissed?.Invoke();
        }
    }

    private async Task<JsonDocument> ReadDocumentAsync(CancellationToken cancellationToken)
    {
        var header = await ReadExactlyAsync(4, cancellationToken);
        var length = BinaryPrimitives.ReadUInt32LittleEndian(header);
        if (length == 0 || length > MaxFrameBytes)
        {
            throw new InvalidOperationException("HAVEN events pipe sent an invalid frame length.");
        }
        var body = await ReadExactlyAsync((int)length, cancellationToken);
        return JsonDocument.Parse(body);
    }

    private async Task<byte[]> ReadExactlyAsync(int length, CancellationToken cancellationToken)
    {
        var buffer = new byte[length];
        var offset = 0;
        while (offset < length)
        {
            var count = await _pipe!.ReadAsync(buffer.AsMemory(offset, length - offset), cancellationToken);
            if (count == 0)
            {
                throw new EndOfStreamException("HAVEN Core closed the events pipe.");
            }
            offset += count;
        }
        return buffer;
    }

    private async Task DisposePipeAsync()
    {
        _readLoopCts?.Cancel();
        var loop = _readLoop;
        _readLoop = null;
        _readLoopCts = null;
        if (loop is not null)
        {
            try
            {
                await loop.WaitAsync(TimeSpan.FromSeconds(2));
            }
            catch (Exception)
            {
                // Best-effort drain; the pipe is being torn down anyway.
            }
        }
        if (_pipe is not null)
        {
            try
            {
                await _pipe.DisposeAsync();
            }
            catch (Exception)
            {
                // A broken pipe disposes best-effort.
            }
            _pipe = null;
        }
    }

    public async ValueTask DisposeAsync()
    {
        _watchdog?.Dispose();
        _watchdog = null;
        await DisposePipeAsync();
    }
}
