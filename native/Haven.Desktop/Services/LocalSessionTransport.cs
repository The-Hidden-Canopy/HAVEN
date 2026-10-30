using System.Text.Json;
using Haven.SessionContract;

namespace Haven.Desktop;

public sealed class LocalSessionTransport(HavenCoreClient client) : ISessionTransport
{
    public Task<JsonElement> CallAsync(string method, IReadOnlyDictionary<string, object?> arguments,
        string requestId, CancellationToken cancellationToken) =>
        client.LocalWorkflowAsync("sessions.call", new { tool = method, request_id = requestId, arguments }, cancellationToken);
}
