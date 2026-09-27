using System.Text.Json;

namespace Haven.Desktop;

/// <summary>
/// MCP endpoint settings (Build/Ship/Shape WP2): whether this device serves
/// the local <c>POST /mcp</c> transport, and on which loopback port.
/// Persists to <c>%LOCALAPPDATA%/Haven.Desktop/mcp.json</c>; invalid content
/// fails closed to disabled. Native-side by construction: the endpoint lives
/// and dies with this process, so its on/off switch belongs here, not in the
/// core's durable state.
/// </summary>
public sealed class McpSettings
{
    public const int DefaultPort = 8741;

    private static string SettingsPath =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Haven.Desktop",
            "mcp.json");

    public bool Enabled { get; private set; }
    public int Port { get; private set; } = DefaultPort;

    public static McpSettings Load()
    {
        var settings = new McpSettings();
        try
        {
            if (!File.Exists(SettingsPath))
            {
                return settings;
            }
            using var document = JsonDocument.Parse(File.ReadAllText(SettingsPath));
            var root = document.RootElement;
            if (root.TryGetProperty("enabled", out var enabled) && enabled.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                settings.Enabled = enabled.GetBoolean();
            }
            if (root.TryGetProperty("port", out var port) && port.TryGetInt32(out var value) && value is > 0 and < 65536)
            {
                settings.Port = value;
            }
        }
        catch (Exception)
        {
            // Fail closed: a corrupt settings file means "endpoint off".
            return new McpSettings();
        }
        return settings;
    }

    public void Save(bool enabled, int port)
    {
        Enabled = enabled;
        Port = port is > 0 and < 65536 ? port : DefaultPort;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
            File.WriteAllText(
                SettingsPath,
                JsonSerializer.Serialize(new { enabled = Enabled, port = Port }));
        }
        catch (Exception)
        {
            // Settings persistence is best-effort; the running host keeps its
            // applied configuration either way.
        }
    }
}
