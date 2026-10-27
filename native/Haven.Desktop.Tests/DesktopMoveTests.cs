using System.Text.Json;
using Xunit;

namespace Haven.Desktop.Tests;

[CollectionDefinition("Native state routing", DisableParallelization = true)]
public sealed class NativeStateRoutingCollection { }

[Collection("Native state routing")]
public sealed class DesktopMoveTests
{
    [Fact]
    public async Task Confirmed_core_move_routes_settings_to_the_new_installation()
    {
        var temporary = Path.Combine(Path.GetTempPath(), "haven-native-move-" + Guid.NewGuid().ToString("N"));
        var oldRoot = Path.Combine(temporary, "old");
        var nextRoot = Path.Combine(temporary, "new");
        var saved = new[] { "HAVEN_DATA_DIR", "HAVEN_DESKTOP_DATA_DIR", "HAVEN_DESKTOP_STATE_POLICY" }
            .ToDictionary(name => name, Environment.GetEnvironmentVariable);
        var pipe = "haven-state-move-" + Guid.NewGuid().ToString("N");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        try
        {
            Environment.SetEnvironmentVariable("HAVEN_DATA_DIR", oldRoot);
            Environment.SetEnvironmentVariable("HAVEN_DESKTOP_DATA_DIR", Path.Combine(oldRoot, "desktop"));
            Environment.SetEnvironmentVariable("HAVEN_DESKTOP_STATE_POLICY", "installation");
            var server = Task.Run(async () =>
            {
                await using var stream = await FakeCoreServer.AcceptAndAuthenticateAsync(pipe, timeout.Token);
                for (var step = 0; step < 2; step++)
                {
                    using var request = JsonDocument.Parse(await FakeCoreServer.ReadFrameAsync(stream, timeout.Token));
                    Assert.Equal(step == 0 ? "setup.data_dir" : "system.diagnostics", request.RootElement.GetProperty("method").GetString());
                    object result = step == 0 ? new { ok = true } : new { diagnostics = new { data_dir = nextRoot } };
                    await FakeCoreServer.WriteFrameAsync(stream, JsonSerializer.Serialize(new
                    {
                        ok = true, result, request_id = request.RootElement.GetProperty("request_id").GetString()
                    }), timeout.Token);
                }
            }, timeout.Token);
            await using var client = new HavenCoreClient(pipe, "synthetic-core-token");
            await client.ConnectAsync(timeout.Token);
            await client.ChooseSetupDataDirAsync(nextRoot, timeout.Token);
            await server;
            Assert.Equal(Path.Combine(nextRoot, "desktop"), DesktopPaths.Root);
            new McpSettings().Save(true, 12345);
            var appearance = new ThemeService();
            await appearance.SaveAsync();
            Assert.True(File.Exists(Path.Combine(nextRoot, "desktop", "mcp.json")));
            Assert.True(File.Exists(Path.Combine(nextRoot, "desktop", "settings.json")));
            Assert.False(Directory.Exists(oldRoot));
        }
        finally
        {
            foreach (var pair in saved) Environment.SetEnvironmentVariable(pair.Key, pair.Value);
            if (Directory.Exists(temporary)) Directory.Delete(temporary, recursive: true);
        }
    }
}
