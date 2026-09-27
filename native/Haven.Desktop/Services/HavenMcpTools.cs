using System.ComponentModel;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using ModelContextProtocol;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;

namespace Haven.Desktop;

/// <summary>
/// The MCP tool surface (Build/Ship/Shape WP3). Every tool is a thin forward
/// to the core's <c>external_agents.tools.call</c>: the JSON arguments cross
/// the pipe unchanged and the gateway/admission/authority decision happens
/// in Python, exactly once (ADR-001/002).
///
/// Tool names use underscores at the MCP boundary (the MCP name grammar is
/// <c>[a-zA-Z0-9_-]</c>) and map to the canonical dotted names the gateway
/// knows: <c>haven_world_get</c> -> <c>haven.world.get</c>, etc.
/// </summary>
[McpServerToolType]
public sealed class HavenMcpTools
{
    private readonly HavenMcpCoreBridge _core;
    private readonly IHttpContextAccessor _httpContext;

    public HavenMcpTools(HavenMcpCoreBridge core, IHttpContextAccessor httpContext)
    {
        _core = core;
        _httpContext = httpContext;
    }

    [McpServerTool(Name = "haven_world_get"),
     Description("Read HAVEN's current world state (rooms, devices, people, contexts, pending confirmations). Requires the world.read scope.")]
    public Task<CallToolResult> WorldGet(CancellationToken cancellationToken) =>
        CallAsync("haven.world.get", null, cancellationToken);

    [McpServerTool(Name = "haven_rooms_list"),
     Description("List HAVEN's rooms with their devices and present people. Requires the world.read scope.")]
    public Task<CallToolResult> RoomsList(CancellationToken cancellationToken) =>
        CallAsync("haven.rooms.list", null, cancellationToken);

    [McpServerTool(Name = "haven_action_request"),
     Description("Request a governed device action, e.g. turn a light off. HAVEN's authority engine decides every time: the action may execute, pend for owner confirmation (returns a request_id for haven_action_confirm), or be denied with a reason. Requires the actions.request scope.")]
    public Task<CallToolResult> ActionRequest(
        [Description("The target device id, e.g. \"office_light\"")] string device_id,
        [Description("The service to invoke, e.g. \"light.turn_off\"")] string service,
        [Description("Optional service parameters, e.g. {\"brightness_pct\": 40}")] JsonElement? parameters = null,
        CancellationToken cancellationToken = default) =>
        CallAsync(
            "haven.action.request",
            new Dictionary<string, object?>
            {
                ["device_id"] = device_id,
                ["service"] = service,
                ["parameters"] = parameters,
            },
            cancellationToken);

    [McpServerTool(Name = "haven_action_confirm"),
     Description("Confirm a pending HAVEN action that this connection requested earlier. Only the requesting principal can confirm its own pending handle. Requires the actions.request scope.")]
    public Task<CallToolResult> ActionConfirm(
        [Description("The pending request id returned by haven_action_request")] string request_id,
        CancellationToken cancellationToken = default) =>
        CallAsync("haven.action.confirm", new Dictionary<string, object?> { ["request_id"] = request_id }, cancellationToken);

    [McpServerTool(Name = "haven_action_deny"),
     Description("Deny a pending HAVEN action that this connection requested earlier. Requires the actions.request scope.")]
    public Task<CallToolResult> ActionDeny(
        [Description("The pending request id returned by haven_action_request")] string request_id,
        CancellationToken cancellationToken = default) =>
        CallAsync("haven.action.deny", new Dictionary<string, object?> { ["request_id"] = request_id }, cancellationToken);

    private async Task<CallToolResult> CallAsync(string tool, object? arguments, CancellationToken cancellationToken)
    {
        var context = _httpContext.HttpContext;
        var credential = context?.Items["haven.credential"] as string ?? "";
        var subject = context?.Items["haven.subject"] as string;
        var subjectLabel = context?.Items["haven.subject_label"] as string;
        var session = context?.Items["haven.session"] as string;
        JsonElement result;
        try
        {
            result = await _core.CallToolAsync(
                credential, subject, subjectLabel, session, tool, arguments, cancellationToken);
        }
        catch (Exception ex) when (ex is not McpException)
        {
            // Transport-level failure (pipe down, core restarting): a protocol
            // error, not a governed answer.
            throw new McpException($"HAVEN core is unreachable: {ex.Message}", ex);
        }
        // Gateway denials (typed external.* codes) and executor refusals are
        // both reported as tool errors with the full envelope as content, so
        // the calling agent can read the machine code and human message.
        var isError = result.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False;
        return new CallToolResult
        {
            IsError = isError,
            Content = { new TextContentBlock { Text = result.GetRawText() } },
        };
    }
}
