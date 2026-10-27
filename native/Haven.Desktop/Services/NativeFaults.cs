using System.Text.Json;
using System.Text.RegularExpressions;

namespace Haven.Desktop;

/// <summary>Native view of HAVEN fault schema v1; original evidence stays local.</summary>
public static class NativeFaults
{
    private static readonly object Gate = new();

    public static string ErrorText(JsonElement response)
    {
        if (response.ValueKind == JsonValueKind.Object
            && response.TryGetProperty("fault", out var fault)
            && fault.TryGetProperty("code", out var code)
            && fault.TryGetProperty("message", out var message)
            && Regex.IsMatch(code.GetString() ?? "", @"^HAVEN\.[A-Z0-9_.]{1,120}$"))
        {
            var correlation = fault.TryGetProperty("correlation_id", out var id) ? id.GetString() : null;
            return $"[{code.GetString()}] {message.GetString()}" + (correlation is null ? "" : $" (reference {correlation})");
        }
        return "[HAVEN.UNKNOWN.UNEXPECTED] The operation could not complete. Inspect local diagnostics.";
    }

    public static string Message(Exception exception)
    {
        if (exception is ReportedFaultException) return exception.Message;
        var code = exception is ArgumentException ? "HAVEN.CONFIG.INVALID" :
            exception is IOException or TimeoutException ? "HAVEN.IPC.UNAVAILABLE" : "HAVEN.UNKNOWN.UNEXPECTED";
        var correlation = Guid.NewGuid().ToString("N");
        try
        {
            lock (Gate)
            {
                var root = DesktopPaths.Root;
                Directory.CreateDirectory(root);
                var path = Path.Combine(root, "native-incidents.json");
                var rows = File.Exists(path)
                    ? JsonSerializer.Deserialize<Dictionary<string, Incident>>(File.ReadAllText(path)) ?? new()
                    : new Dictionary<string, Incident>();
                if (!rows.TryGetValue(code, out var row)) rows[code] = row = new Incident();
                row.Count++;
                row.FirstSeen ??= DateTimeOffset.UtcNow.ToString("O");
                row.LastSeen = DateTimeOffset.UtcNow.ToString("O");
                row.Correlations = row.Correlations.Append(correlation).TakeLast(16).ToList();
                File.WriteAllText(path, JsonSerializer.Serialize(rows));
                var evidence = Path.Combine(root, "native-private-evidence.log");
                if (!File.Exists(evidence) || new FileInfo(evidence).Length < 1_048_576)
                    File.AppendAllText(evidence, $"{row.LastSeen} {correlation} {exception}\n");
            }
        }
        catch { /* Never fall back to a different, potentially live directory. */ }
        return $"[{code}] The operation could not complete. Reconnect or inspect local diagnostics. (reference {correlation})";
    }

    public sealed class Incident
    {
        public long Count { get; set; }
        public string? FirstSeen { get; set; }
        public string? LastSeen { get; set; }
        public List<string> Correlations { get; set; } = new();
    }

    public sealed class ReportedFaultException : InvalidOperationException
    {
        public ReportedFaultException(JsonElement response) : base(ErrorText(response)) { }
    }
}
