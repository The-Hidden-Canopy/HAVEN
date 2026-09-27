using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace Haven.Desktop;

/// <summary>
/// The native-hosted MCP endpoint (Build/Ship/Shape WP2): a loopback-only
/// Kestrel host inside this process serving <c>POST /mcp</c> via the official
/// ModelContextProtocol SDK (spec section 9.1 prefers a maintained SDK over
/// hand-rolled JSON-RPC). This class is a protocol adapter only -- every
/// tool call crosses the IPC pipe to the Python gateway, where admission and
/// the authority engine decide (ADR-001/002). No bearer credential, no MCP.
/// </summary>
public sealed class McpHostService : IAsyncDisposable
{
    private readonly Func<(string PipeName, string Token)?> _coreEndpoint;
    private WebApplication? _app;
    private int _port;

    public McpHostService(Func<(string PipeName, string Token)?> coreEndpoint)
    {
        _coreEndpoint = coreEndpoint;
    }

    public string StatusText { get; private set; } = "Off";
    public int? ListeningPort => _app is null ? null : _port;

    /// <summary>Apply settings: start, stop, or restart-on-port-change.</summary>
    public async Task ApplyAsync(McpSettings settings)
    {
        if (!settings.Enabled)
        {
            await StopAsync();
            StatusText = "Off";
            return;
        }
        if (_app is not null && _port == settings.Port)
        {
            return; // already serving the requested endpoint
        }
        await StopAsync();
        var endpoint = _coreEndpoint();
        if (endpoint is null)
        {
            StatusText = "Core not connected";
            return;
        }
        try
        {
            var app = BuildApp(endpoint.Value.PipeName, endpoint.Value.Token, settings.Port);
            await app.StartAsync();
            _app = app;
            _port = settings.Port;
            StatusText = $"Listening on http://127.0.0.1:{settings.Port}/mcp";
        }
        catch (Exception ex)
        {
            StatusText = $"Could not start: {ex.Message}";
        }
    }

    public async Task StopAsync()
    {
        var app = _app;
        _app = null;
        if (app is not null)
        {
            try
            {
                await app.StopAsync();
                await app.DisposeAsync();
            }
            catch (Exception)
            {
                // Shutdown is best-effort; a wedged host must not hold the UI.
            }
        }
    }

    public async ValueTask DisposeAsync() => await StopAsync();

    private static WebApplication BuildApp(string pipeName, string token, int port)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            Args = Array.Empty<string>(),
            ContentRootPath = AppContext.BaseDirectory,
        });
        // The desktop app's own diagnostics own the log surface; a WinExe has
        // no console and Kestrel chatter has nowhere useful to go.
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options => options.ListenLocalhost(port));
        builder.Services.AddHttpContextAccessor();
        builder.Services.AddSingleton(new HavenMcpCoreBridge(pipeName, token));
        builder.Services.AddMcpServer().WithHttpTransport().WithTools<HavenMcpTools>();
        var app = builder.Build();
        app.Use(BearerGate);
        app.MapMcp("/mcp");
        return app;
    }

    /// <summary>
    /// Edge check: a bearer credential must be *present* before any MCP
    /// message is processed (validity is enforced per tool call by the
    /// gateway). The provider-asserted subject rides optional headers the
    /// transport forwards verbatim; HAVEN never trusts them beyond a binding.
    /// </summary>
    private static async Task BearerGate(HttpContext context, RequestDelegate next)
    {
        if (!context.Request.Path.StartsWithSegments("/mcp"))
        {
            await next(context);
            return;
        }
        const string prefix = "Bearer ";
        var header = context.Request.Headers.Authorization.ToString();
        var credential = header.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
            ? header[prefix.Length..].Trim()
            : "";
        if (credential.Length == 0)
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            context.Response.ContentType = "application/json";
            await context.Response.WriteAsync("{\"error\":\"a bearer credential is required\"}");
            return;
        }
        context.Items["haven.credential"] = credential;
        var subject = context.Request.Headers["X-HAVEN-Subject"].ToString();
        if (!string.IsNullOrWhiteSpace(subject))
        {
            context.Items["haven.subject"] = subject.Trim();
        }
        var subjectLabel = context.Request.Headers["X-HAVEN-Subject-Label"].ToString();
        if (!string.IsNullOrWhiteSpace(subjectLabel))
        {
            context.Items["haven.subject_label"] = subjectLabel.Trim();
        }
        var session = context.Request.Headers["Mcp-Session-Id"].ToString();
        if (!string.IsNullOrWhiteSpace(session))
        {
            context.Items["haven.session"] = session.Trim();
        }
        await next(context);
    }
}

/// <summary>
/// The MCP host's own IPC channel to the core. Independent of the UI's
/// <see cref="HavenCoreClient"/> (whose lifecycle the ConnectionService
/// owns): connects lazily, and on a dead pipe reconnects and retries once.
/// </summary>
public sealed class HavenMcpCoreBridge
{
    private readonly string _pipeName;
    private readonly string _token;
    private readonly SemaphoreSlim _clientLock = new(1, 1);
    private HavenCoreClient? _client;

    public HavenMcpCoreBridge(string pipeName, string token)
    {
        _pipeName = pipeName;
        _token = token;
    }

    public async Task<JsonElement> CallToolAsync(
        string credential,
        string? subject,
        string? subjectLabel,
        string? sessionId,
        string tool,
        object? arguments,
        CancellationToken cancellationToken)
    {
        try
        {
            return await CallOnceAsync(credential, subject, subjectLabel, sessionId, tool, arguments, cancellationToken);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or EndOfStreamException)
        {
            await ResetClientAsync();
            return await CallOnceAsync(credential, subject, subjectLabel, sessionId, tool, arguments, cancellationToken);
        }
    }

    private async Task<JsonElement> CallOnceAsync(
        string credential,
        string? subject,
        string? subjectLabel,
        string? sessionId,
        string tool,
        object? arguments,
        CancellationToken cancellationToken)
    {
        var client = await GetClientAsync(cancellationToken);
        return await client.CallExternalAgentToolAsync(
            credential,
            tool,
            $"mcp-{Guid.NewGuid():N}",
            subject,
            subjectLabel,
            sessionId,
            arguments,
            cancellationToken);
    }

    private async Task<HavenCoreClient> GetClientAsync(CancellationToken cancellationToken)
    {
        await _clientLock.WaitAsync(cancellationToken);
        try
        {
            _client ??= await ConnectFreshAsync(cancellationToken);
            return _client;
        }
        finally
        {
            _clientLock.Release();
        }
    }

    private async Task ResetClientAsync()
    {
        await _clientLock.WaitAsync();
        try
        {
            if (_client is not null)
            {
                await _client.DisconnectAsync();
                _client = null;
            }
        }
        finally
        {
            _clientLock.Release();
        }
    }

    private async Task<HavenCoreClient> ConnectFreshAsync(CancellationToken cancellationToken)
    {
        var client = new HavenCoreClient(_pipeName, _token);
        await client.ConnectAsync(cancellationToken);
        return client;
    }
}
