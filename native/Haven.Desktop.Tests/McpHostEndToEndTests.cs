using System.Buffers.Binary;
using System.IO.Pipes;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ModelContextProtocol.Client;
using Xunit;

namespace Haven.Desktop.Tests;

public sealed class McpHostEndToEndTests
{
    [Fact]
    public async Task Official_client_discovers_and_calls_a_tool_through_the_native_host()
    {
        var pipeName = $"haven-mcp-test-{Guid.NewGuid():N}";
        await using var core = new FakeMcpCore(pipeName, "core-token");
        await core.StartAsync();

        await using var host = new Haven.Desktop.McpHostService(() => (pipeName, "core-token"));
        var port = FreePort();
        var settings = new Haven.Desktop.McpSettings();
        settings.Save(enabled: true, port: port);
        await host.ApplyAsync(settings);

        Assert.Equal($"Listening on http://127.0.0.1:{port}/mcp", host.StatusText);

        using var http = new HttpClient();
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "mcp-bearer");
        http.DefaultRequestHeaders.Add("X-HAVEN-Subject", "subject-1");
        http.DefaultRequestHeaders.Add("X-HAVEN-Subject-Label", "Test client");
        var transport = new HttpClientTransport(
            new HttpClientTransportOptions
            {
                Endpoint = new Uri($"http://127.0.0.1:{port}/mcp"),
                TransportMode = HttpTransportMode.StreamableHttp,
                EnableStandaloneGetStream = false,
            },
            http,
            NullLoggerFactory.Instance,
            ownsHttpClient: false);
        await using var client = await McpClient.CreateAsync(
            transport,
            clientOptions: null,
            NullLoggerFactory.Instance);

        var tools = await client.ListToolsAsync();
        Assert.Equal(
            new[]
            {
                "haven_action_confirm",
                "haven_action_deny",
                "haven_action_request",
                "haven_rooms_list",
                "haven_world_get",
            },
            tools.Select(tool => tool.Name).OrderBy(name => name));

        var result = await client.CallToolAsync("haven_world_get", new Dictionary<string, object?>());
        Assert.False(result.IsError);
        Assert.Contains(result.Content, content => content is ModelContextProtocol.Protocol.TextContentBlock block
            && block.Text.Contains("haven.world.get", StringComparison.Ordinal));

        var rooms = await client.CallToolAsync("haven_rooms_list", new Dictionary<string, object?>());
        Assert.False(rooms.IsError);

        var request = await client.CallToolAsync(
            "haven_action_request",
            new Dictionary<string, object?>
            {
                ["device_id"] = "proof-device",
                ["service"] = "light.turn_off",
                ["parameters"] = new Dictionary<string, object?> { ["brightness_pct"] = 40 },
            });
        Assert.False(request.IsError);

        var confirm = await client.CallToolAsync(
            "haven_action_confirm",
            new Dictionary<string, object?> { ["request_id"] = "proof-request" });
        Assert.False(confirm.IsError);

        var deny = await client.CallToolAsync(
            "haven_action_deny",
            new Dictionary<string, object?> { ["request_id"] = "proof-request" });
        Assert.False(deny.IsError);

        var call = await core.LastToolCall;
        Assert.Equal("external_agents.tools.call", call.GetProperty("method").GetString());
        Assert.Equal(5, core.ToolCallCount);
        Assert.Equal(
            new[]
            {
                "haven.world.get",
                "haven.rooms.list",
                "haven.action.request",
                "haven.action.confirm",
                "haven.action.deny",
            },
            core.ToolCalls.Select(item => item.GetProperty("params").GetProperty("tool").GetString()));
        Assert.Equal("mcp-bearer", call.GetProperty("params").GetProperty("credential").GetString());
        Assert.Equal("subject-1", call.GetProperty("params").GetProperty("subject").GetString());
        var actionRequest = core.ToolCalls[2].GetProperty("params");
        Assert.Equal("proof-device", actionRequest.GetProperty("arguments").GetProperty("device_id").GetString());
        Assert.Equal("light.turn_off", actionRequest.GetProperty("arguments").GetProperty("service").GetString());

        await host.StopAsync();
        Assert.Equal("Off", host.StatusText);
    }

    [Fact]
    public async Task Missing_bearer_is_rejected_before_mcp_initialization()
    {
        var pipeName = $"haven-mcp-auth-{Guid.NewGuid():N}";
        await using var core = new FakeMcpCore(pipeName, "core-token");
        await core.StartAsync();

        await using var host = new Haven.Desktop.McpHostService(() => (pipeName, "core-token"));
        var port = FreePort();
        var settings = new Haven.Desktop.McpSettings();
        settings.Save(enabled: true, port: port);
        await host.ApplyAsync(settings);

        using var http = new HttpClient();
        using var response = await http.PostAsync(
            $"http://127.0.0.1:{port}/mcp",
            new StringContent("{}", Encoding.UTF8, "application/json"));

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Equal(0, core.ToolCallCount);
    }

    private static int FreePort()
    {
        using var listener = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }

    private sealed class FakeMcpCore : IAsyncDisposable
    {
        private readonly string _pipeName;
        private readonly string _token;
        private readonly TaskCompletionSource<JsonElement> _lastToolCall =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        private readonly List<JsonElement> _toolCalls = new();
        private readonly CancellationTokenSource _stop = new();
        private NamedPipeServerStream? _server;

        public FakeMcpCore(string pipeName, string token)
        {
            _pipeName = pipeName;
            _token = token;
        }

        public Task<JsonElement> LastToolCall => _lastToolCall.Task;
        public int ToolCallCount { get; private set; }
        public IReadOnlyList<JsonElement> ToolCalls => _toolCalls;

        public Task StartAsync()
        {
            _ = Task.Run(ServeAsync);
            return Task.CompletedTask;
        }

        private async Task ServeAsync()
        {
            try
            {
                _server = new NamedPipeServerStream(
                    _pipeName,
                    PipeDirection.InOut,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous);
                await _server.WaitForConnectionAsync(_stop.Token);
                while (!_stop.IsCancellationRequested)
                {
                    var request = await ReadFrameAsync(_server, _stop.Token);
                    using var document = JsonDocument.Parse(request);
                    var root = document.RootElement;
                    var method = root.GetProperty("method").GetString();
                    var requestId = root.GetProperty("request_id").GetString() ?? "";
                    if (method == "host.authenticate")
                    {
                        Assert.Equal(_token, root.GetProperty("params").GetProperty("token").GetString());
                        await WriteFrameAsync(_server, $"{{\"version\":\"haven-ipc-1\",\"kind\":\"response\",\"request_id\":\"{requestId}\",\"ok\":true,\"result\":{{\"authenticated\":true}}}}", _stop.Token);
                        continue;
                    }

                    if (method == "external_agents.tools.call")
                    {
                        ToolCallCount++;
                        var detached = root.Clone();
                        _toolCalls.Add(detached);
                        _lastToolCall.TrySetResult(detached);
                        await WriteFrameAsync(
                            _server,
                            $"{{\"version\":\"haven-ipc-1\",\"kind\":\"response\",\"request_id\":\"{requestId}\",\"ok\":true,\"result\":{{\"ok\":true,\"tool\":\"haven.world.get\",\"rooms\":[]}}}}",
                            _stop.Token);
                        continue;
                    }

                    await WriteFrameAsync(
                        _server,
                        $"{{\"version\":\"haven-ipc-1\",\"kind\":\"response\",\"request_id\":\"{requestId}\",\"ok\":false,\"error\":\"unexpected method\"}}",
                        _stop.Token);
                }
            }
            catch (OperationCanceledException) when (_stop.IsCancellationRequested)
            {
            }
            catch (IOException) when (_stop.IsCancellationRequested)
            {
            }
        }

        public async ValueTask DisposeAsync()
        {
            _stop.Cancel();
            if (_server is not null)
            {
                await _server.DisposeAsync();
            }
            try
            {
                await _lastToolCall.Task.WaitAsync(TimeSpan.FromMilliseconds(1));
            }
            catch (TimeoutException)
            {
            }
            _stop.Dispose();
        }

        private static async Task<byte[]> ReadFrameAsync(Stream stream, CancellationToken cancellationToken)
        {
            var header = await ReadExactlyAsync(stream, 4, cancellationToken);
            var length = BinaryPrimitives.ReadUInt32LittleEndian(header);
            return await ReadExactlyAsync(stream, checked((int)length), cancellationToken);
        }

        private static async Task WriteFrameAsync(Stream stream, string json, CancellationToken cancellationToken)
        {
            var body = Encoding.UTF8.GetBytes(json);
            var frame = new byte[4 + body.Length];
            BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(0, 4), (uint)body.Length);
            body.CopyTo(frame.AsSpan(4));
            await stream.WriteAsync(frame, cancellationToken);
            await stream.FlushAsync(cancellationToken);
        }

        private static async Task<byte[]> ReadExactlyAsync(Stream stream, int length, CancellationToken cancellationToken)
        {
            var buffer = new byte[length];
            var offset = 0;
            while (offset < length)
            {
                var count = await stream.ReadAsync(buffer.AsMemory(offset, length - offset), cancellationToken);
                if (count == 0)
                {
                    throw new EndOfStreamException();
                }
                offset += count;
            }
            return buffer;
        }
    }
}
