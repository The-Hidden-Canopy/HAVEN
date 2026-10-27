using Xunit;

namespace Haven.Desktop.Tests;

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
}
