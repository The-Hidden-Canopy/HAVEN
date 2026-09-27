using Microsoft.UI.Xaml;

namespace Haven.Desktop;

/// <summary>
/// Settings -> MCP endpoint (Build/Ship/Shape WP2): the on/off/port surface
/// for the native-hosted <c>POST /mcp</c> transport this device serves to
/// external clients, plus the host lifecycle (start once the core is
/// connected, stop at window close). All admission and mutation decisions
/// stay in the core across the pipe; this only manages the listener.
/// </summary>
public sealed partial class MainWindow
{
    private McpHostService? _mcpHost;
    private McpSettings _mcpSettings = McpSettings.Load();

    private async Task EnsureMcpHostAsync()
    {
        _mcpHost ??= new McpHostService(() =>
            _connectionPipeName is not null && _connectionToken is not null
                ? (_connectionPipeName, _connectionToken)
                : null);
        await _mcpHost.ApplyAsync(_mcpSettings);
    }

    private void RefreshMcpEndpointControls()
    {
        McpEndpointToggle.IsOn = _mcpSettings.Enabled;
        McpEndpointPort.Text = _mcpSettings.Port.ToString();
        McpEndpointStatusText.Text = _mcpHost?.StatusText ?? "Off";
    }

    private async void OnMcpEndpointApplyClicked(object sender, RoutedEventArgs args)
    {
        var enabled = McpEndpointToggle.IsOn;
        if (!int.TryParse(McpEndpointPort.Text.Trim(), out var port) || port is <= 0 or >= 65536)
        {
            McpEndpointStatusText.Text = "Port must be a number between 1 and 65535.";
            return;
        }
        _mcpSettings.Save(enabled, port);
        await EnsureMcpHostAsync();
        RefreshMcpEndpointControls();
    }
}
