using System.Globalization;
using System.Text.Json;

namespace Haven.Desktop;

public static class DiagnosticsFormatting
{
    // Core reports elapsed seconds as a fractional JSON number, not an Int64.
    public static string Uptime(JsonElement diagnostics) =>
        diagnostics.ValueKind == JsonValueKind.Object
        && diagnostics.TryGetProperty("uptime_seconds", out var value)
        && value.ValueKind == JsonValueKind.Number
        && value.TryGetDouble(out var seconds)
        && double.IsFinite(seconds)
        && seconds >= 0
            ? seconds.ToString("0.0", CultureInfo.InvariantCulture) + " s"
            : "unavailable";
}
