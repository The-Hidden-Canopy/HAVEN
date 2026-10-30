using System.Text.Json;

namespace Haven.Desktop;

public sealed record SessionPending(string SessionId, string CommandId);

public static class SessionPendingJournal
{
    private static string Location => DesktopPaths.File("session-pending.json");
    public static SessionPending? Read()
    {
        if (!File.Exists(Location)) return null;
        return JsonSerializer.Deserialize<SessionPending>(File.ReadAllText(Location))
            ?? throw new InvalidOperationException("Pending session command state needs inspection.");
    }
    public static void Write(SessionPending pending)
    {
        Directory.CreateDirectory(DesktopPaths.Root);
        var temporary = Location + ".tmp";
        File.WriteAllText(temporary,JsonSerializer.Serialize(pending));
        File.Move(temporary,Location,true);
    }
    public static void Clear() => File.Delete(Location);
}
