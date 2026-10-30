using System.Net;
using System.Text;
using Xunit;

namespace Haven.Desktop.Tests;

public sealed class LatchBoundaryTests
{
    private sealed class Capture : HttpMessageHandler
    {
        public string? Target;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        {
            Target=request.RequestUri!.PathAndQuery;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) {Content=new StringContent("{}",Encoding.UTF8,"application/json")});
        }
    }
    [Theory]
    [InlineData("http://127.attacker.example:8787")]
    [InlineData("http://127.1:8787")]
    [InlineData("http://2130706433:8787")]
    [InlineData("http://localhost:0")]
    [InlineData("http://localhost:8787/path")]
    [InlineData("http://user@localhost:8787")]
    public void OriginIsAnUnambiguousLoopbackOrigin(string origin) =>
        Assert.Throws<ArgumentException>(()=>new LatchClient(new Uri(origin),new Capture()));

    [Fact]
    public async Task IdsAreOpaqueAndEncodedOnce()
    {
        var capture=new Capture();using var client=new LatchClient(new Uri("http://127.0.0.1:8787"),capture);
        await client.GetCommitmentAsync("café /literal%2F+?");
        Assert.Equal("/v1/commitments/caf%C3%A9%20%2Fliteral%252F%2B%3F",capture.Target);
        await Assert.ThrowsAsync<ArgumentException>(()=>client.GetCommitmentAsync("../escape"));
    }
    [Fact]
    public void ProductionTransportDoesNotFollowRedirectsOrUseProxies()
    {
        using var handler=LatchClient.NewHandler();Assert.False(handler.AllowAutoRedirect);Assert.False(handler.UseProxy);
    }
}
