using System.Text.Json;
using Xunit;

namespace Haven.Desktop.Tests;

public sealed class DiagnosticsFormattingTests
{
    [Theory]
    [InlineData("{\"uptime_seconds\":0}", "0.0 s")]
    [InlineData("{\"uptime_seconds\":12.345}", "12.3 s")]
    [InlineData("{\"uptime_seconds\":42}", "42.0 s")]
    [InlineData("{\"uptime_seconds\":1e400}", "unavailable")]
    [InlineData("{\"uptime_seconds\":-1}", "unavailable")]
    [InlineData("{\"uptime_seconds\":\"12.3\"}", "unavailable")]
    [InlineData("{}", "unavailable")]
    [InlineData("null", "unavailable")]
    public void Core_elapsed_time_renders_without_integer_conversion(string json, string expected)
    {
        using var document = JsonDocument.Parse(json);
        Assert.Equal(expected, DiagnosticsFormatting.Uptime(document.RootElement));
    }
}
