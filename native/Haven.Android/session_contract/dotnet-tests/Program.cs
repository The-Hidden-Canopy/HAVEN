using Haven.SessionContract;
using System.Text.Json;

var wire = new FakeTransport(); var client = new SessionClient(wire);
await Fails(() => client.SubmitAsync("s", "hello", "stable"), "capability_unavailable");
await client.CapabilitiesAsync("cap");
await client.SubmitAsync("s", "hello", "stable");
if (wire.LastRequest != "stable" || wire.Count != 2) throw new Exception("request ID or retry");
await client.ReplayAsync("s", 0, "replay");
wire.Gap = true;
await Fails(() => client.ReplayAsync("s", 0, "gap"), "replay_gap");
wire.Gap = false; wire.WrongSession = true;
await Fails(() => client.SnapshotAsync("s", "snapshot"), "incompatible_session");
wire.WrongSession = false; wire.BadVersion = true;
await Fails(() => client.CapabilitiesAsync("bad"), "incompatible_session");
await Fails(() => client.SubmitAsync("s", "hello", "blocked"), "capability_unavailable");
Console.WriteLine("Desktop client contract checks passed");
static async Task Fails(Func<Task<JsonElement>> call, string expected) { try { await call(); } catch (InvalidOperationException e) when (e.Message == expected) { return; } throw new Exception("Expected " + expected); }
sealed class FakeTransport : ISessionTransport {
 public int Count; public string? LastRequest; public bool Gap, WrongSession, BadVersion;
 public Task<JsonElement> CallAsync(string method, IReadOnlyDictionary<string, object?> args, string id, CancellationToken ct) {
  ct.ThrowIfCancellationRequested(); Count++; LastRequest = id;
  object response = method.EndsWith("capabilities") ? new {ok=true, contract=BadVersion ? "wrong" : SessionClient.Version, operations=new[]{"snapshot","replay","submit"}, guarantees=new[]{"durable_replay","idempotent_commands"}} : method.EndsWith("replay") ? new {ok=true, contract=SessionClient.Version, session_id="s", events=new[]{new {seq=Gap ? 2 : 1}}, next_cursor=Gap ? 2 : 1} : new {ok=true, contract=SessionClient.Version, session_id=WrongSession ? "other" : "s"};
  return Task.FromResult(JsonSerializer.SerializeToElement(response));
 }
}
