using Xunit;

namespace Haven.Desktop.Tests;

[Collection("Native state routing")]
public sealed class DesktopStateIsolationTests
{
    [Fact]
    public void Explicit_native_root_never_resolves_the_real_special_folder()
    {
        var path = Path.Combine(Path.GetTempPath(), "haven-state-" + Guid.NewGuid().ToString("N"));
        Assert.Equal(path, DesktopPaths.ResolveRoot(path, null, () => throw new Exception("real profile accessed")));
        Assert.Equal(Path.Combine(path, "desktop"), DesktopPaths.ResolveRoot(null, path, () => throw new Exception("real profile accessed")));
        Assert.Throws<ArgumentException>(() => DesktopPaths.ResolveRoot("relative-path", null, () => throw new Exception("fallback accessed")));
        Assert.Throws<ArgumentException>(() => DesktopPaths.ResolveRoot("", path, () => throw new Exception("fallback accessed")));
    }

    [Fact]
    public void Mcp_state_round_trips_only_at_the_injected_path_and_corruption_stays_off()
    {
        var directory = Path.Combine(Path.GetTempPath(), "haven-state-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "mcp.json");
        try
        {
            var settings = new McpSettings(path);
            Assert.False(settings.Enabled);
            settings.Save(true, 12345);
            var read = McpSettings.Load(path);
            Assert.True(read.Enabled);
            Assert.Equal(12345, read.Port);
            File.WriteAllText(path, "corrupt test settings");
            Assert.False(McpSettings.Load(path).Enabled);
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [Fact]
    public void Native_faults_keep_original_text_private_and_retain_group_counts()
    {
        var root = Path.Combine(Path.GetTempPath(), "haven-fault-" + Guid.NewGuid().ToString("N"));
        var savedRoot = Environment.GetEnvironmentVariable("HAVEN_DESKTOP_DATA_DIR");
        var savedPolicy = Environment.GetEnvironmentVariable("HAVEN_DESKTOP_STATE_POLICY");
        try
        {
            Environment.SetEnvironmentVariable("HAVEN_DESKTOP_DATA_DIR", root);
            Environment.SetEnvironmentVariable("HAVEN_DESKTOP_STATE_POLICY", "explicit");
            const string marker = "PRIVATE-SYNTHETIC-NATIVE-DETAIL";
            for (var i = 0; i < 2; i++)
            {
                var text = NativeFaults.Message(new InvalidOperationException(marker), "core.startup");
                Assert.Contains("HAVEN.CORE.STARTUP_FAILED", text);
                Assert.DoesNotContain(marker, text);
            }
            using var journal = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "native-incidents.json")));
            var row = journal.RootElement.EnumerateObject().Single().Value;
            Assert.Equal(1, row.GetProperty("SchemaVersion").GetInt32());
            Assert.Equal(2, row.GetProperty("Count").GetInt32());
            Assert.Equal("possible", row.GetProperty("Confidence").GetString());
            Assert.NotEqual(row.GetProperty("FirstCorrelation").GetString(), row.GetProperty("LastCorrelation").GetString());
            Assert.Contains(marker, File.ReadAllText(Path.Combine(root, "native-private-evidence.log")));
        }
        finally
        {
            Environment.SetEnvironmentVariable("HAVEN_DESKTOP_DATA_DIR", savedRoot);
            Environment.SetEnvironmentVariable("HAVEN_DESKTOP_STATE_POLICY", savedPolicy);
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }
}
