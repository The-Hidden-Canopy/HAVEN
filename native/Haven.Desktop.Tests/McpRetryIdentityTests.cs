using System.Text.Json;
using Xunit;

namespace Haven.Desktop.Tests;

public sealed class McpRetryIdentityTests
{
    [Fact]
    public async Task Lost_reply_reconnect_reuses_the_external_request_identity()
    {
        var pipe = "haven-retry-" + Guid.NewGuid().ToString("N");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        var identities = new List<string?>();
        var server = Task.Run(async () =>
        {
            for (var attempt = 0; attempt < 2; attempt++)
            {
                await using var stream = await FakeCoreServer.AcceptAndAuthenticateAsync(pipe, timeout.Token);
                using var request = JsonDocument.Parse(await FakeCoreServer.ReadFrameAsync(stream, timeout.Token));
                identities.Add(request.RootElement.GetProperty("params").GetProperty("external_request_id").GetString());
                // First admission's reply is lost. A retry must carry the same
                // identity so Core's existing dedup/reconciliation can apply.
                if (attempt == 0) continue;
                var id = request.RootElement.GetProperty("request_id").GetString();
                await FakeCoreServer.WriteFrameAsync(stream, JsonSerializer.Serialize(new
                {
                    version = "haven-ipc-1", kind = "response", request_id = id, ok = true,
                    result = new { ok = true, reconciled = true }
                }), timeout.Token);
            }
        }, timeout.Token);
        var bridge = new HavenMcpCoreBridge(pipe, "synthetic-core-token");
        var result = await bridge.CallToolAsync("synthetic-external-token", null, null, null,
            "haven.action.request", new { device_id = "synthetic-device", service = "light.turn_off" }, timeout.Token);
        await server;
        Assert.True(result.GetProperty("reconciled").GetBoolean());
        Assert.Equal(2, identities.Count);
        Assert.Equal(identities[0], identities[1]);
    }
}
