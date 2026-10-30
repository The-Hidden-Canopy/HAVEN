using System.Net;
using System.Text;
using System.Text.Json;
using Xunit;

namespace Haven.Desktop.Tests;

/// <summary>Focused tests for the Latch loopback adapter. A fake
/// `HttpMessageHandler` captures every request (method, route, body) and
/// replays scripted responses, so each test pins one route/payload or one
/// failure mode of the thin HTTP layer — the same read + propose-only
/// contract Latch's own `integrations/haven` adapter enforces structurally
/// on the Python side.</summary>
public sealed class LatchClientTests
{
    private sealed record CapturedRequest(string Method, string PathAndQuery, string? Body);

    private sealed class FakeLatchHandler : HttpMessageHandler
    {
        private readonly List<CapturedRequest> _requests = new();
        private readonly Queue<Func<CapturedRequest, CancellationToken, Task<HttpResponseMessage>>> _responses = new();

        public IReadOnlyList<CapturedRequest> Requests => _requests;
        public bool FailWithHttpRequestException { get; set; }

        public void EnqueueJson(string json, HttpStatusCode status = HttpStatusCode.OK) =>
            _responses.Enqueue((_, _) => Task.FromResult(new HttpResponseMessage(status)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json"),
            }));

        public void Enqueue(Func<CapturedRequest, CancellationToken, Task<HttpResponseMessage>> responder) =>
            _responses.Enqueue(responder);

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var body = request.Content is null
                ? null
                : request.Content.ReadAsStringAsync(cancellationToken).Result;
            var captured = new CapturedRequest(
                request.Method.Method,
                request.RequestUri!.PathAndQuery,
                body);
            lock (_requests)
            {
                _requests.Add(captured);
            }
            if (FailWithHttpRequestException)
            {
                throw new HttpRequestException("connection refused (simulated daemon stop)");
            }
            if (_responses.Count == 0)
            {
                throw new InvalidOperationException("Test did not enqueue a response.");
            }
            return _responses.Dequeue()(captured, cancellationToken);
        }
    }

    private static LatchClient CreateClient(FakeLatchHandler handler, string baseUrl = "http://127.0.0.1:8787") =>
        new(new Uri(baseUrl), handler, timeout: TimeSpan.FromSeconds(5));

    private static string PayloadField(string json, string field)
    {
        using var document = JsonDocument.Parse(json);
        return document.RootElement.GetProperty(field).GetRawText();
    }

    // ---- Routes and payloads -------------------------------------------------

    [Fact]
    public async Task GetCommitmentsAsync_hits_the_commitments_route()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"commitments\":[]}");
        using var client = CreateClient(handler);

        var result = await client.GetCommitmentsAsync();

        var request = Assert.Single(handler.Requests);
        Assert.Equal("GET", request.Method);
        Assert.Equal("/v1/commitments", request.PathAndQuery);
        Assert.True(result.TryGetProperty("commitments", out _));
    }

    [Fact]
    public async Task GetCommitmentAsync_escapes_the_id_into_the_route()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"id\":\"c 1/ä\"}");
        using var client = CreateClient(handler);

        await client.GetCommitmentAsync("c 1/ä");

        var request = Assert.Single(handler.Requests);
        Assert.Equal("GET", request.Method);
        Assert.Equal("/v1/commitments/" + Uri.EscapeDataString("c 1/ä"), request.PathAndQuery);
    }

    [Fact]
    public async Task GetAccessAsync_with_staleOnly_sends_the_stale_only_where_filter()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("[]");
        using var client = CreateClient(handler);

        await client.GetAccessAsync(staleOnly: true);

        var request = Assert.Single(handler.Requests);
        Assert.Equal("/v1/access?where=" + Uri.EscapeDataString("{\"stale_only\":true}"), request.PathAndQuery);
    }

    [Fact]
    public async Task GetAccessAsync_without_staleOnly_omits_the_filter()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("[]");
        using var client = CreateClient(handler);

        await client.GetAccessAsync();

        Assert.Equal("/v1/access", Assert.Single(handler.Requests).PathAndQuery);
    }

    [Fact]
    public async Task GetReviewProposalsAsync_hits_the_schedules_proposals_route()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"proposals\":[]}");
        using var client = CreateClient(handler);

        await client.GetReviewProposalsAsync();

        var request = Assert.Single(handler.Requests);
        Assert.Equal("GET", request.Method);
        Assert.Equal("/v1/schedules/proposals", request.PathAndQuery);
    }

    [Fact]
    public async Task GetActionPlansAsync_lists_plans_without_touching_state()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"plans\":[]}");
        using var client = CreateClient(handler);

        await client.GetActionPlansAsync();

        var request = Assert.Single(handler.Requests);
        Assert.Equal("GET", request.Method);
        Assert.Equal("/v1/actions", request.PathAndQuery);
    }

    [Fact]
    public async Task GetActionPlanAsync_hits_the_plan_route()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"plan_id\":\"p1\",\"state\":\"PROPOSED\"}");
        using var client = CreateClient(handler);

        await client.GetActionPlanAsync("p1");

        Assert.Equal("/v1/actions/p1", Assert.Single(handler.Requests).PathAndQuery);
    }

    [Fact]
    public async Task GetEvidenceAsync_includes_content_by_default_and_omits_it_on_request()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"id\":\"e1\"}");
        handler.EnqueueJson("{\"id\":\"e1\"}");
        using var client = CreateClient(handler);

        await client.GetEvidenceAsync("e1");
        await client.GetEvidenceAsync("e1", includeContent: false);

        Assert.Equal("/v1/evidence/e1?content=1", handler.Requests[0].PathAndQuery);
        Assert.Equal("/v1/evidence/e1", handler.Requests[1].PathAndQuery);
    }

    [Fact]
    public async Task PlanCancellationAsync_posts_the_idempotent_propose_payload()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"plan_id\":\"plan-9\",\"state\":\"PROPOSED\"}");
        using var client = CreateClient(handler);

        var result = await client.PlanCancellationAsync("commitment-42");

        var request = Assert.Single(handler.Requests);
        Assert.Equal("POST", request.Method);
        Assert.Equal("/v1/actions/plan", request.PathAndQuery);
        Assert.Equal("\"cancel_subscription\"", PayloadField(request.Body!, "operation"));
        Assert.Equal("{}", PayloadField(request.Body!, "target"));
        Assert.Equal("\"commitment-42\"", PayloadField(request.Body!, "commitment_id"));
        Assert.Equal(
            "\"haven:cancel_subscription:commitment-42\"",
            PayloadField(request.Body!, "idempotency_key"));
        // The plan stays PROPOSED inside Latch; the response is passed
        // through untouched, never "verified" by transport success.
        Assert.Equal("PROPOSED", result.GetProperty("state").GetString());
    }

    [Fact]
    public async Task PlanCancellationAsync_reuses_the_same_idempotency_key_when_repeated()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{}");
        handler.EnqueueJson("{}");
        using var client = CreateClient(handler);

        await client.PlanCancellationAsync("commitment-7");
        await client.PlanCancellationAsync("commitment-7");

        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(handler.Requests[0].Body, handler.Requests[1].Body);
        Assert.Equal(
            "\"haven:cancel_subscription:commitment-7\"",
            PayloadField(handler.Requests[1].Body!, "idempotency_key"));
    }

    // ---- Configuration ---------------------------------------------------------

    [Fact]
    public void Constructor_rejects_non_loopback_base_uris()
    {
        var handler = new FakeLatchHandler();
        Assert.Throws<ArgumentException>(() => new LatchClient(new Uri("http://192.168.1.10:8787"), handler));
        Assert.Throws<ArgumentException>(() => new LatchClient(new Uri("http://10.0.0.2:8787"), handler));
        Assert.Throws<ArgumentException>(() => new LatchClient(new Uri("https://latch.example.com"), handler));
    }

    [Fact]
    public void Constructor_rejects_non_http_schemes()
    {
        var handler = new FakeLatchHandler();
        Assert.Throws<ArgumentException>(() => new LatchClient(new Uri("ftp://127.0.0.1:8787"), handler));
        Assert.Throws<ArgumentException>(() => new LatchClient(new Uri("file:///127.0.0.1/db"), handler));
    }

    [Fact]
    public void Constructor_accepts_all_loopback_spellings()
    {
        var handler = new FakeLatchHandler();
        using var a = new LatchClient(new Uri("http://127.0.0.1:8787"), handler);
        using var b = new LatchClient(new Uri("http://localhost:9999"), handler);
        using var c = new LatchClient(new Uri("http://[::1]:8787"), handler);
        Assert.Equal("127.0.0.1", a.BaseUri.Host);
        Assert.Equal("localhost", b.BaseUri.Host);
        // System.Uri keeps the brackets on IPv6 literals.
        Assert.Equal("[::1]", c.BaseUri.Host);
    }

    [Fact]
    public void CreateDefault_honors_a_loopback_environment_override_and_demo_flag()
    {
        var variables = new Dictionary<string, string?>
        {
            [LatchClient.BaseUrlEnvironmentVariable] = "http://localhost:9999",
            [LatchClient.DemoEnvironmentVariable] = "1",
        };
        using var client = LatchClient.CreateDefault(
            key => variables.TryGetValue(key!, out var value) ? value : null,
            handler: new FakeLatchHandler());

        Assert.Equal("http://localhost:9999/", client.BaseUri.ToString());
        Assert.True(client.IsDemoMode);
    }

    [Fact]
    public void CreateDefault_defaults_to_the_latchd_loopback_address()
    {
        using var client = LatchClient.CreateDefault(_ => null, handler: new FakeLatchHandler());

        Assert.Equal(new Uri(LatchClient.DefaultBaseUrl), client.BaseUri);
        Assert.False(client.IsDemoMode);
    }

    [Fact]
    public void CreateDefault_throws_for_a_non_loopback_environment_override()
    {
        var variables = new Dictionary<string, string?>
        {
            [LatchClient.BaseUrlEnvironmentVariable] = "http://203.0.113.7:8787",
        };
        Assert.Throws<ArgumentException>(() => LatchClient.CreateDefault(
            key => variables.TryGetValue(key!, out var value) ? value : null,
            handler: new FakeLatchHandler()));
    }

    [Fact]
    public void CreateDefault_throws_for_a_malformed_environment_override()
    {
        var variables = new Dictionary<string, string?>
        {
            [LatchClient.BaseUrlEnvironmentVariable] = "not a uri",
        };
        Assert.Throws<ArgumentException>(() => LatchClient.CreateDefault(
            key => variables.TryGetValue(key!, out var value) ? value : null,
            handler: new FakeLatchHandler()));
    }

    // ---- Availability, disconnect/reconnect, errors ----------------------------

    [Fact]
    public async Task IsAvailableAsync_probes_a_real_api_route_and_returns_true_on_success()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("[]");
        using var client = CreateClient(handler);

        Assert.True(await client.IsAvailableAsync());

        var request = Assert.Single(handler.Requests);
        Assert.Equal("GET", request.Method);
        Assert.Equal("/v1/commitments", request.PathAndQuery);
    }

    [Fact]
    public async Task IsAvailableAsync_returns_false_when_the_daemon_is_down_and_true_again_on_reconnect()
    {
        var handler = new FakeLatchHandler { FailWithHttpRequestException = true };
        using var client = CreateClient(handler);

        Assert.False(await client.IsAvailableAsync());
        await Assert.ThrowsAsync<LatchUnavailableException>(() => client.GetCommitmentsAsync());

        // latchd comes back: the same client (same BaseUri) sees it again.
        handler.FailWithHttpRequestException = false;
        handler.EnqueueJson("[]");
        Assert.True(await client.IsAvailableAsync());

        handler.EnqueueJson("{\"commitments\":[]}");
        var result = await client.GetCommitmentsAsync();
        Assert.True(result.TryGetProperty("commitments", out _));
    }

    [Fact]
    public async Task Non_success_status_maps_to_LatchUnavailableException_with_the_status()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"error\":\"no such commitment\"}", HttpStatusCode.NotFound);
        handler.EnqueueJson("{\"error\":\"boom\"}", HttpStatusCode.InternalServerError);
        using var client = CreateClient(handler);

        var notFound = await Assert.ThrowsAsync<LatchUnavailableException>(
            () => client.GetCommitmentAsync("missing"));
        Assert.Equal(HttpStatusCode.NotFound, notFound.StatusCode);

        var serverError = await Assert.ThrowsAsync<LatchUnavailableException>(
            () => client.GetCommitmentsAsync());
        Assert.Equal(HttpStatusCode.InternalServerError, serverError.StatusCode);
    }

    [Fact]
    public async Task Malformed_json_maps_to_LatchUnavailableException()
    {
        var handler = new FakeLatchHandler();
        handler.Enqueue((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("this is not json", Encoding.UTF8, "application/json"),
        }));
        using var client = CreateClient(handler);

        await Assert.ThrowsAsync<LatchUnavailableException>(() => client.GetCommitmentsAsync());
    }

    [Fact]
    public async Task A_timeout_maps_to_LatchUnavailableException()
    {
        var handler = new FakeLatchHandler();
        handler.Enqueue(async (_, cancellationToken) =>
        {
            // Observe the caller's token so HttpClient's own timeout
            // cancellation actually lands, the way a real socket would.
            await Task.Delay(TimeSpan.FromSeconds(30), cancellationToken);
            return new HttpResponseMessage(HttpStatusCode.OK);
        });
        using var client = new LatchClient(
            new Uri("http://127.0.0.1:8787"), handler, timeout: TimeSpan.FromMilliseconds(50));

        await Assert.ThrowsAsync<LatchUnavailableException>(() => client.GetCommitmentsAsync());
    }

    // ---- Authorization handoff ---------------------------------------------------

    [Fact]
    public void GetAuthorizationHandoffUri_is_the_root_uri_with_only_a_plan_id_query()
    {
        using var client = CreateClient(new FakeLatchHandler(), baseUrl: "http://127.0.0.1:8787");

        var uri = client.GetAuthorizationHandoffUri("plan 1/ä");

        Assert.NotNull(uri);
        Assert.Equal("/", uri!.AbsolutePath);
        Assert.Equal("http", uri.Scheme);
        Assert.Equal("127.0.0.1", uri.Host);
        Assert.Equal(8787, uri.Port);
        var query = uri.Query.TrimStart('?');
        Assert.StartsWith("plan_id=", query);
        Assert.DoesNotContain("&", query);
        // Round-trips to the exact plan id however Uri displays escapes.
        Assert.Equal("plan 1/ä", Uri.UnescapeDataString(query["plan_id=".Length..]));
    }

    [Fact]
    public void GetAuthorizationHandoffUri_returns_null_for_empty_plan_ids()
    {
        using var client = CreateClient(new FakeLatchHandler());
        Assert.Null(client.GetAuthorizationHandoffUri(null));
        Assert.Null(client.GetAuthorizationHandoffUri(""));
        Assert.Null(client.GetAuthorizationHandoffUri("   "));
    }

    // ---- Handoff availability (root UI probe) -----------------------------------

    private static HttpResponseMessage Html(string body, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(body, Encoding.UTF8, "text/html") };

    [Fact]
    public async Task IsHandoffAvailableAsync_is_true_when_the_root_serves_html()
    {
        var handler = new FakeLatchHandler();
        handler.Enqueue((_, _) => Task.FromResult(Html("<!doctype html><html><body>Latch</body></html>")));
        using var client = CreateClient(handler);

        Assert.True(await client.IsHandoffAvailableAsync());

        var request = Assert.Single(handler.Requests);
        Assert.Equal("GET", request.Method);
        Assert.Equal("/", request.PathAndQuery);
    }

    [Fact]
    public async Task IsHandoffAvailableAsync_is_false_when_the_root_404s_without_web_root()
    {
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"error\":\"not found\"}", HttpStatusCode.NotFound);
        using var client = CreateClient(handler);

        Assert.False(await client.IsHandoffAvailableAsync());
    }

    [Fact]
    public async Task IsHandoffAvailableAsync_is_false_when_the_root_answers_non_html()
    {
        // The API answering at / is not the desktop UI: a JSON body must not
        // pass the probe even with a success status.
        var handler = new FakeLatchHandler();
        handler.EnqueueJson("{\"status\":\"ok\"}");
        using var client = CreateClient(handler);

        Assert.False(await client.IsHandoffAvailableAsync());
    }

    [Fact]
    public async Task IsHandoffAvailableAsync_is_false_when_the_daemon_is_down_and_recovers_with_ui()
    {
        var handler = new FakeLatchHandler { FailWithHttpRequestException = true };
        using var client = CreateClient(handler);

        Assert.False(await client.IsHandoffAvailableAsync());

        handler.FailWithHttpRequestException = false;
        handler.Enqueue((_, _) => Task.FromResult(Html("<html>Latch</html>")));
        Assert.True(await client.IsHandoffAvailableAsync());
    }

    // ---- Read + propose-only contract ---------------------------------------------

    [Theory]
    [InlineData("authorize")]
    [InlineData("execute")]
    [InlineData("verify")]
    public void LatchClient_exposes_no_methods_named_for_lifecycle_advancement(string forbidden)
    {
        var methods = typeof(LatchClient)
            .GetMethods(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance
                | System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.DeclaredOnly);
        Assert.All(methods, method =>
            Assert.DoesNotContain(forbidden, method.Name, StringComparison.OrdinalIgnoreCase));
    }

    // ---- Refresh-race protection ---------------------------------------------------

    [Fact]
    public void Refresh_tokens_increase_monotonically_and_supersede_in_flight_ones()
    {
        var coordinator = new LatchRefreshCoordinator();
        var first = coordinator.BeginRefresh();
        var second = coordinator.BeginRefresh();

        Assert.True(second > first);
        Assert.False(coordinator.IsCurrent(first));
        Assert.True(coordinator.IsCurrent(second));
        Assert.Equal(second, coordinator.Current);
    }

    [Fact]
    public void Invalidate_supersedes_every_in_flight_refresh_without_starting_one()
    {
        var coordinator = new LatchRefreshCoordinator();
        var token = coordinator.BeginRefresh();
        var before = coordinator.Current;

        coordinator.Invalidate();

        Assert.False(coordinator.IsCurrent(token));
        Assert.Equal(before + 1, coordinator.Current);
    }

    [Fact]
    public async Task Concurrent_refresh_starters_receive_unique_monotonic_tokens()
    {
        var coordinator = new LatchRefreshCoordinator();
        var tokens = await Task.WhenAll(Enumerable.Range(0, 64)
            .Select(_ => Task.Run(() => coordinator.BeginRefresh())));

        var ordered = tokens.OrderBy(token => token).ToArray();
        Assert.Equal(ordered, tokens.Distinct().OrderBy(token => token).ToArray());
        Assert.Equal(Enumerable.Range(1, 64).Select(i => (long)i), ordered);
    }
}
