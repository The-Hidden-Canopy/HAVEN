using System.Net.Sockets;
using System.Text.RegularExpressions;
using System.Net;
using System.Text;
using System.Text.Json;

namespace Haven.Desktop;

/// <summary>Thrown when the Latch loopback daemon cannot be reached, times
/// out, or answers with a non-success status. Distinguishes "Latch is
/// disconnected" from ordinary argument/logic errors so the UI can render
/// its disconnected/error states instead of crashing.</summary>
public sealed class LatchUnavailableException : Exception
{
    public LatchUnavailableException(string message, Exception? inner = null, HttpStatusCode? statusCode = null)
        : base(message, inner)
    {
        StatusCode = statusCode;
    }

    public HttpStatusCode? StatusCode { get; }
}

/// <summary>Thin read + propose-only adapter over latchd's loopback
/// HTTP/JSON transport (spec: HAVEN talks to
/// `http://127.0.0.1:8787`, overridable via `HAVEN_LATCH_BASE_URL` to
/// another *loopback* address only; `HAVEN_LATCH_DEMO=1` marks the data
/// as a synthetic demo so the UI can label it).
///
/// It exposes no authorize/execute/verify operation: HAVEN proposes plans
/// (`PlanCancellationAsync`) and hands authorization off to Latch's own
/// desktop UI via <see cref="GetAuthorizationHandoffUri"/>. Transport
/// success is never treated as verification; the action lifecycle states
/// (Proposed/Authorized/Executed/Verified) live entirely inside Latch.
/// </summary>
public sealed class LatchClient : IDisposable, IAsyncDisposable
{
    public const string DefaultBaseUrl = "http://127.0.0.1:8787";
    public const string BaseUrlEnvironmentVariable = "HAVEN_LATCH_BASE_URL";
    public const string DemoEnvironmentVariable = "HAVEN_LATCH_DEMO";
    public const string CancelSubscriptionOperation = "cancel_subscription";

    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(5);
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    private static readonly Regex OriginPattern = new(
        @"\A(?<scheme>https?)://(?<host>\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.]+)(?::(?<port>[0-9]+))?/?\z",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    public static HttpClientHandler NewHandler() => new() { AllowAutoRedirect = false, UseProxy = false };

    public static Uri ValidateOrigin(string origin)
    {
        var match = OriginPattern.Match(origin ?? "");
        if (!match.Success) throw new ArgumentException("Expected a loopback HTTP(S) origin", nameof(origin));
        var host = match.Groups["host"].Value;
        var portText = match.Groups["port"].Value;
        if (portText.Length > 0 && (!int.TryParse(portText, out var port) || port < 1 || port > 65535))
            throw new ArgumentException("Invalid Latch port", nameof(origin));
        if (!string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase))
        {
            bool bracketed = host.StartsWith('[') && host.EndsWith(']');
            string numeric = bracketed ? host[1..^1] : host;
            if (!IPAddress.TryParse(numeric, out var address) || !IPAddress.IsLoopback(address) ||
                (bracketed && (address.AddressFamily != AddressFamily.InterNetworkV6 || !address.Equals(IPAddress.IPv6Loopback))) ||
                (!bracketed && (address.AddressFamily != AddressFamily.InterNetwork || address.ToString() != numeric)))
                throw new ArgumentException("Expected a canonical loopback address", nameof(origin));
        }
        return new Uri(origin!.TrimEnd('/') + "/", UriKind.Absolute);
    }

    private static string Id(string value)
    {
        if (string.IsNullOrEmpty(value) || value.Contains('\\') || value.Any(char.IsControl) ||
            value.Split('/').Any(part => part is "" or "." or ".."))
            throw new ArgumentException("Ambiguous or invalid Latch identifier", nameof(value));
        try { _ = new UTF8Encoding(false, true).GetByteCount(value); }
        catch (EncoderFallbackException ex) { throw new ArgumentException("Invalid UTF-16 identifier", nameof(value), ex); }
        return value;
    }
    private static string Segment(string value) => Uri.EscapeDataString(Id(value));

    private readonly HttpClient _http;

    /// <summary>Test-friendly constructor: every input explicit. Throws
    /// <see cref="ArgumentException"/> unless <paramref name="baseUri"/> is
    /// an absolute http(s) URI on a loopback host.</summary>
    public LatchClient(Uri baseUri, HttpMessageHandler handler, TimeSpan? timeout = null, bool isDemoMode = false)
    {
        BaseUri = RequireLoopbackBaseUri(baseUri, nameof(baseUri));
        _http = new HttpClient(handler) { Timeout = timeout ?? DefaultTimeout };
        IsDemoMode = isDemoMode;
    }

    /// <summary>The validated loopback base URI the client talks to. The UI
    /// uses this to build the authorization handoff and to reason about
    /// availability.</summary>
    public Uri BaseUri { get; }

    /// <summary>True when `HAVEN_LATCH_DEMO=1` was set at creation: the UI
    /// must label anything it renders as synthetic demo data.</summary>
    public bool IsDemoMode { get; }

    /// <summary>Production factory: resolves the base URL from
    /// `HAVEN_LATCH_BASE_URL` (falling back to <see cref="DefaultBaseUrl"/>)
    /// and demo labeling from `HAVEN_LATCH_DEMO`, both overridable for
    /// tests. A non-loopback or malformed override is a configuration error
    /// and throws <see cref="ArgumentException"/> at startup, not later.
    /// </summary>
    public static LatchClient CreateDefault(
        Func<string?, string?>? getEnvironmentVariable = null,
        HttpMessageHandler? handler = null,
        TimeSpan? timeout = null)
    {
        getEnvironmentVariable ??= name => Environment.GetEnvironmentVariable(name!);
        var raw = getEnvironmentVariable(BaseUrlEnvironmentVariable);
        Uri baseUri;
        if (string.IsNullOrWhiteSpace(raw))
        {
            baseUri = new Uri(DefaultBaseUrl);
        }
        else if (!Uri.TryCreate(raw, UriKind.Absolute, out var parsed))
        {
            throw new ArgumentException(
                $"{BaseUrlEnvironmentVariable} is not an absolute URI: '{raw}'.",
                BaseUrlEnvironmentVariable);
        }
        else
        {
            baseUri = parsed;
        }

        var demo = string.Equals(getEnvironmentVariable(DemoEnvironmentVariable), "1", StringComparison.Ordinal);
        return new LatchClient(baseUri, handler ?? NewHandler(), timeout, demo);
    }

    public Task<JsonElement> GetCommitmentsAsync(CancellationToken cancellationToken = default) =>
        GetAsync("/v1/commitments", cancellationToken);

    public Task<JsonElement> GetCommitmentAsync(string commitmentId, CancellationToken cancellationToken = default)
    {
        _ = Id(commitmentId);
        return GetAsync($"/v1/commitments/{Segment(commitmentId)}", cancellationToken);
    }

    public Task<JsonElement> GetAccessAsync(bool staleOnly = false, CancellationToken cancellationToken = default)
    {
        // access.query filters on a JSON `where` document; `stale_only` is
        // the field the store understands (see Latch core access_store).
        var path = staleOnly
            ? "/v1/access?where=" + Uri.EscapeDataString("{\"stale_only\":true}")
            : "/v1/access";
        return GetAsync(path, cancellationToken);
    }

    public Task<JsonElement> GetReviewProposalsAsync(CancellationToken cancellationToken = default) =>
        GetAsync("/v1/schedules/proposals", cancellationToken);

    /// <summary>Lists action plans known to Latch. Read-only: this never
    /// advances a plan's state.</summary>
    public Task<JsonElement> GetActionPlansAsync(CancellationToken cancellationToken = default) =>
        GetAsync("/v1/actions", cancellationToken);

    public Task<JsonElement> GetActionPlanAsync(string planId, CancellationToken cancellationToken = default)
    {
        _ = Id(planId);
        return GetAsync($"/v1/actions/{Segment(planId)}", cancellationToken);
    }

    public Task<JsonElement> GetEvidenceAsync(
        string evidenceId,
        bool includeContent = true,
        CancellationToken cancellationToken = default)
    {
        _ = Id(evidenceId);
        var path = $"/v1/evidence/{Segment(evidenceId)}";
        if (includeContent)
        {
            // latchd treats the mere presence of `content` as the flag.
            path += "?content=1";
        }
        return GetAsync(path, cancellationToken);
    }

    /// <summary>Proposes (never performs) a cancellation plan for a
    /// commitment. The idempotency key is deterministic per commitment so
    /// repeated proposals from a double-clicked button collapse into one
    /// plan server-side. The result stays PROPOSED inside Latch until a
    /// user authorizes it through Latch's own desktop flow.
    /// </summary>
    public async Task<JsonElement> PlanCancellationAsync(
        string commitmentId,
        CancellationToken cancellationToken = default)
    {
        _ = Id(commitmentId);
        var payload = new
        {
            commitment_id = commitmentId,
            operation = CancelSubscriptionOperation,
            target = new { },
            idempotency_key = $"haven:{CancelSubscriptionOperation}:{commitmentId}",
        };
        using var content = new StringContent(
            JsonSerializer.Serialize(payload, JsonOptions),
            Encoding.UTF8,
            "application/json");
        using var response = await SendAsync(
            () => new HttpRequestMessage(HttpMethod.Post, new Uri(BaseUri, "/v1/actions/plan")) { Content = content },
            cancellationToken);
        return await ReadBodyAsync(response, cancellationToken);
    }

    /// <summary>True when latchd's API actually answers. Probes a real,
    /// read-only API route rather than claiming anything about Latch's web
    /// UI, and never throws: an unreachable daemon is simply "not
    /// available" (yet).</summary>
    public async Task<bool> IsAvailableAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await SendAsync(
                () => new HttpRequestMessage(HttpMethod.Get, new Uri(BaseUri, "/v1/commitments")),
                cancellationToken,
                throwOnNonSuccess: false);
            return response.IsSuccessStatusCode;
        }
        catch (LatchUnavailableException)
        {
            return false;
        }
    }

    /// <summary>True when Latch's own desktop UI is actually served at the
    /// service root - i.e. latchd runs with a <c>--web-root</c>. The
    /// authorization handoff deep link 404s without it, so the UI probes
    /// before enabling the handoff instead of opening a dead link. Requires
    /// a success status AND an HTML body (the API alone answers JSON, not
    /// the UI). Never throws.</summary>
    public async Task<bool> IsHandoffAvailableAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await SendAsync(
                () => new HttpRequestMessage(HttpMethod.Get, new Uri(BaseUri, "/")),
                cancellationToken,
                throwOnNonSuccess: false);
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }
            var mediaType = response.Content.Headers.ContentType?.MediaType ?? "";
            if (!string.Equals(mediaType, "text/html", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            return body.Contains("<html", StringComparison.OrdinalIgnoreCase)
                || body.Contains("<!doctype", StringComparison.OrdinalIgnoreCase);
        }
        catch (LatchUnavailableException)
        {
            return false;
        }
    }

    /// <summary>Deep link into Latch's own desktop UI so the user can
    /// authorize a proposed plan there. The URI is always the service root
    /// with exactly a `plan_id` query — no path, no other parameters — and
    /// is null for an empty plan id.</summary>
    public Uri? GetAuthorizationHandoffUri(string? planId)
    {
        if (string.IsNullOrWhiteSpace(planId))
        {
            return null;
        }
        return new UriBuilder(BaseUri.Scheme, BaseUri.Host, BaseUri.Port)
        {
            Path = "/",
            Query = "plan_id=" + Segment(planId),
        }.Uri;
    }

    private async Task<JsonElement> GetAsync(string path, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(
            () => new HttpRequestMessage(HttpMethod.Get, new Uri(BaseUri, path)),
            cancellationToken);
        return await ReadBodyAsync(response, cancellationToken);
    }

    private async Task<HttpResponseMessage> SendAsync(
        Func<HttpRequestMessage> requestFactory,
        CancellationToken cancellationToken,
        bool throwOnNonSuccess = true)
    {
        using var request = requestFactory();
        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, cancellationToken);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new LatchUnavailableException(
                $"Latch at {BaseUri} did not answer within {_http.Timeout}.");
        }
        catch (HttpRequestException ex)
        {
            throw new LatchUnavailableException(
                $"Latch at {BaseUri} is unreachable: {ex.Message}", ex);
        }

        if (throwOnNonSuccess && !response.IsSuccessStatusCode)
        {
            var status = response.StatusCode;
            response.Dispose();
            throw new LatchUnavailableException(
                $"Latch at {BaseUri} returned {(int)status} ({status}).",
                statusCode: status);
        }
        return response;
    }

    private static async Task<JsonElement> ReadBodyAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        try
        {
            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken),
                cancellationToken: cancellationToken);
            return document.RootElement.Clone();
        }
        catch (JsonException ex)
        {
            throw new LatchUnavailableException("Latch returned a malformed JSON response.", ex);
        }
    }

    private static Uri RequireLoopbackBaseUri(Uri baseUri, string paramName)
    {
        if (baseUri is null)
        {
            throw new ArgumentNullException(paramName);
        }
        return ValidateOrigin(baseUri.OriginalString);
    }

    public void Dispose() => _http.Dispose();

    public ValueTask DisposeAsync()
    {
        Dispose();
        return ValueTask.CompletedTask;
    }
}
