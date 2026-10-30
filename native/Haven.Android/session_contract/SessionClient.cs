// Optional desktop client seam. Transport authentication and provider ownership remain with the host.
using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace Haven.SessionContract;

public sealed class SessionFailureException(string code, bool notExecuted) : InvalidOperationException(code)
{
    public bool NotExecuted { get; } = notExecuted;
}

public interface ISessionTransport
{
    Task<JsonElement> CallAsync(string method, IReadOnlyDictionary<string, object?> arguments,
        string requestId, CancellationToken cancellationToken);
}

public sealed class SessionClient
{
    public const string Version = "haven-session-1";
    private readonly ISessionTransport transport;
    private HashSet<string>? operations;
    private HashSet<string>? guarantees;
    public SessionClient(ISessionTransport transport) => this.transport = transport ?? throw new ArgumentNullException(nameof(transport));

    private static string RequiredString(JsonElement value, string key) =>
        value.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.String
            ? item.GetString()! : throw new InvalidOperationException("invalid_response");
    private static HashSet<string> StringSet(JsonElement value, string key)
    {
        if (!value.TryGetProperty(key, out var items) || items.ValueKind != JsonValueKind.Array)
            throw new InvalidOperationException("invalid_capabilities");
        var result = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in items.EnumerateArray())
            if (item.ValueKind != JsonValueKind.String || !result.Add(item.GetString()!))
                throw new InvalidOperationException("invalid_capabilities");
        return result;
    }
    private async Task<JsonElement> CallAsync(string operation, Dictionary<string, object?> arguments,
        string requestId, string? session, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(requestId)) throw new ArgumentException("stable request ID required", nameof(requestId));
        if (operation != "capabilities" && (operations == null || !operations.Contains(operation)
            || (operation == "replay" && !guarantees!.Contains("durable_replay"))
            || ((operation == "submit" || operation == "cancel" || operation == "decide") && !guarantees!.Contains("idempotent_commands"))))
            throw new SessionFailureException("capability_unavailable",true);
        arguments["version"] = Version;
        var response = await transport.CallAsync("haven.session." + operation, arguments, requestId, cancellationToken).ConfigureAwait(false);
        if (response.ValueKind != JsonValueKind.Object || !response.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True)
        {
            var code = response.ValueKind == JsonValueKind.Object && response.TryGetProperty("code",out var failure)
                && failure.ValueKind == JsonValueKind.String ? failure.GetString()! : "session_unavailable_or_denied";
            throw new SessionFailureException(code,code is "access_denied" or "invalid_or_unsupported" or "not_executed" or "stale_approval" or "session_provider_unavailable");
        }
        if (RequiredString(response, "contract") != Version || (session != null && RequiredString(response, "session_id") != session))
            throw new InvalidOperationException("incompatible_session");
        return response.Clone();
    }
    public async Task<JsonElement> CapabilitiesAsync(string requestId, CancellationToken cancellationToken = default)
    {
        operations = guarantees = null;
        var result = await CallAsync("capabilities", new(), requestId, null, cancellationToken).ConfigureAwait(false);
        var ops = StringSet(result, "operations"); var claims = StringSet(result, "guarantees");
        operations = ops; guarantees = claims; return result;
    }
    public Task<JsonElement> SessionsAsync(string id, CancellationToken ct = default) => CallAsync("sessions", new(), id, null, ct);
    public Task<JsonElement> SnapshotAsync(string session, string id, CancellationToken ct = default) => CallAsync("snapshot", new() { ["session_id"] = session }, id, session, ct);
    public Task<JsonElement> MemoryAsync(string session, string id, CancellationToken ct = default) => CallAsync("memory", new() { ["session_id"] = session }, id, session, ct);
    public Task<JsonElement> CommandStatusAsync(string session, string command, string id, CancellationToken ct = default) => CallAsync("command_status", new() { ["session_id"] = session, ["command_id"] = command }, id, session, ct);
    public Task<JsonElement> SubmitAsync(string session, string text, string id, CancellationToken ct = default) => CallAsync("submit", new() { ["session_id"] = session, ["text"] = text }, id, session, ct);
    public Task<JsonElement> CancelAsync(string session, string job, string id, CancellationToken ct = default) => CallAsync("cancel", new() { ["session_id"] = session, ["job_id"] = job }, id, session, ct);
    public Task<JsonElement> DecideAsync(string session, string approval, long revision, bool allow, string id, CancellationToken ct = default) => CallAsync("decide", new() { ["session_id"] = session, ["approval_id"] = approval, ["revision"] = revision, ["allow"] = allow }, id, session, ct);
    public async Task<JsonElement> ReplayAsync(string session, long after, string id, CancellationToken ct = default)
    {
        if (after < 0) throw new ArgumentOutOfRangeException(nameof(after));
        var result = await CallAsync("replay", new() { ["session_id"] = session, ["after"] = after, ["limit"] = 100 }, id, session, ct).ConfigureAwait(false);
        if (!result.TryGetProperty("events", out var events) || events.ValueKind != JsonValueKind.Array) throw new InvalidOperationException("invalid_replay");
        long cursor = after;
        foreach (var item in events.EnumerateArray())
            if (item.ValueKind != JsonValueKind.Object || !item.TryGetProperty("seq", out var seq) || !seq.TryGetInt64(out var next) || cursor == long.MaxValue || next != ++cursor)
                throw new InvalidOperationException("replay_gap");
        if (!result.TryGetProperty("next_cursor", out var end) || !end.TryGetInt64(out var last) || last != cursor) throw new InvalidOperationException("invalid_cursor");
        return result;
    }
}
