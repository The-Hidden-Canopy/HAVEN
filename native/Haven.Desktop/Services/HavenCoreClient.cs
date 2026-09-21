using System.Buffers.Binary;
using System.IO.Pipes;
using System.Text.Json;

namespace Haven.Desktop;

public sealed class HavenCoreClient : IAsyncDisposable
{
    private const string ProtocolVersion = "haven-ipc-1";
    private const int MaxFrameBytes = 1 << 20;
    private const string PipePrefix = "\\\\.\\pipe\\";
    private readonly string _pipeName;
    private readonly string _authToken;
    private readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };
    private NamedPipeClientStream? _pipe;

    public HavenCoreClient(string pipeName, string authToken)
    {
        _pipeName = pipeName.StartsWith(PipePrefix, StringComparison.OrdinalIgnoreCase)
            ? pipeName[PipePrefix.Length..]
            : pipeName;
        _authToken = authToken;
    }

    public async Task ConnectAsync(CancellationToken cancellationToken = default)
    {
        if (_pipe is not null)
        {
            return;
        }
        _pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        await _pipe.ConnectAsync(5000, cancellationToken);
        using var response = await RequestDocumentAsync(
            "host.authenticate",
            new { token = _authToken },
            cancellationToken);
        EnsureSuccess(response);
    }

    public Task<JsonElement> GetStateAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("state.get", new { }, cancellationToken);

    public Task<JsonElement> SearchAsync(string text, CancellationToken cancellationToken = default) =>
        RequestResultAsync("search.query", new { text, limit = 50 }, cancellationToken);

    public Task<JsonElement> GetKnowledgeClaimsAsync(
        bool includeStale = false,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claims", new { include_stale = includeStale }, cancellationToken);

    public Task<JsonElement> GetKnowledgeClaimAsync(
        string claimId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claim", new { claim_id = claimId }, cancellationToken);

    public Task<JsonElement> CorrectKnowledgeClaimAsync(
        string claimId,
        string proposition,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "knowledge.claim.correct",
            new { claim_id = claimId, proposition },
            cancellationToken);

    public Task<JsonElement> MarkKnowledgeClaimStaleAsync(
        string claimId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claim.stale", new { claim_id = claimId }, cancellationToken);

    public Task<JsonElement> ForgetKnowledgeClaimAsync(
        string claimId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claim.forget", new { claim_id = claimId }, cancellationToken);

    public Task<JsonElement> AskAsync(string text, CancellationToken cancellationToken = default) =>
        RequestResultAsync("composer.ask", new { text }, cancellationToken);

    private async Task<JsonElement> RequestResultAsync(
        string method,
        object parameters,
        CancellationToken cancellationToken)
    {
        using var response = await RequestDocumentAsync(method, parameters, cancellationToken);
        EnsureSuccess(response);
        return response.RootElement.GetProperty("result").Clone();
    }

    private async Task<JsonDocument> RequestDocumentAsync(
        string method,
        object parameters,
        CancellationToken cancellationToken)
    {
        if (_pipe is null || !_pipe.IsConnected)
        {
            throw new InvalidOperationException("HAVEN Core is not connected.");
        }
        var requestId = Guid.NewGuid().ToString("N");
        var request = new
        {
            version = ProtocolVersion,
            kind = "request",
            request_id = requestId,
            method,
            @params = parameters,
        };
        var body = JsonSerializer.SerializeToUtf8Bytes(request, _json);
        if (body.Length > MaxFrameBytes)
        {
            throw new InvalidOperationException("HAVEN IPC request is too large.");
        }
        var frame = new byte[4 + body.Length];
        BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(0, 4), (uint)body.Length);
        body.CopyTo(frame.AsSpan(4));
        await _pipe.WriteAsync(frame, cancellationToken);
        await _pipe.FlushAsync(cancellationToken);

        var header = await ReadExactlyAsync(4, cancellationToken);
        var length = BinaryPrimitives.ReadUInt32LittleEndian(header);
        if (length == 0 || length > MaxFrameBytes)
        {
            throw new InvalidOperationException("HAVEN IPC response has an invalid frame length.");
        }
        var responseBody = await ReadExactlyAsync((int)length, cancellationToken);
        return JsonDocument.Parse(responseBody);
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
                throw new EndOfStreamException("HAVEN Core closed the IPC pipe.");
            }
            offset += count;
        }
        return buffer;
    }

    private static void EnsureSuccess(JsonDocument response)
    {
        var root = response.RootElement;
        if (!root.TryGetProperty("ok", out var ok) || !ok.GetBoolean())
        {
            var error = root.TryGetProperty("error", out var errorValue)
                ? errorValue.GetString()
                : "HAVEN Core rejected the IPC request.";
            throw new InvalidOperationException(error);
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_pipe is not null)
        {
            await _pipe.DisposeAsync();
            _pipe = null;
        }
    }
}
