# HAVEN Desktop

This is the native Windows client boundary for HAVEN. It is intentionally a
thin WinUI 3 client: authority, providers, persistence, knowledge, search, and
speech remain in the Python Core.

The client speaks `haven-ipc-1` over a local Windows named pipe. The Python
desktop launcher starts the Core and passes the pipe name plus a one-time
bearer token through an inherited stdin pipe; the token is not placed in the
native process command line. Manual client launches may use the equivalent
`HAVEN_IPC_PIPE` and `HAVEN_IPC_TOKEN` environment variables.

Alongside the request/response pipe, a second push-only pipe
(`haven-events-<installation-id>`, derived from the RPC pipe name) carries
domain-invalidation events and a heartbeat, so open views invalidate without
polling. `EventClientCoordinator` (in the sibling `Haven.Core.Client`
project) owns reconnect/backoff/stall-detection for that channel; `Settings`
shows its live state ("Connected" / "Stalled (last frame …)" /
"Reconnecting…").

In native mode the compatibility HTTP socket is closed after the Python
service graph is composed; the native client communicates only through the
local named pipe. Browser mode continues to expose the existing loopback WebUI,
but only when explicitly requested with `--web`/`--debug-web`.

The Python desktop launcher starts this client by default:

```powershell
python -m haven.desktop
```

**Native is the production surface; WebUI is debug/compatibility only**
(`--web`/`--debug-web`) — see `TASKS.md` Milestones A/B. The native client
covers setup (a full wizard, not a WebUI prerequisite), every life-assistant
domain (Today, Search, Projects, Tasks, People, Memory, Computer,
Communications, Home, Models, Settings/System), and the same authoring paths
the web surface has (room/automation/task/project CRUD, governed device and
resource actions). It is not a thin read-only proof-of-concept; do not assume
a capability is web-only without checking `HavenCoreClient.cs` and the
corresponding `MainWindow.*.cs` partial first. Remaining gaps (interactive
click-through QA, conversation providers, tablet touch QA, and others) are
tracked in `TASKS.md`, not here — this file describes what the client *is*,
not a punch list.

See `docs/qa/native-proof-loop.md` for the hands-on install-to-recovery
verification script this client should pass before a release.

## Build prerequisites

- Windows 10 1809 or later
- .NET 8 SDK
- Visual Studio 2022 with the Windows App SDK / WinUI workload
- A restored `Microsoft.WindowsAppSDK` package matching
  `WindowsAppSDKVersion`

Build the Debug client from the repository root with:

```powershell
dotnet build native\Haven.Desktop\Haven.Desktop.csproj --configuration Debug -p:Platform=x64
```

`Haven.Desktop.csproj` references `..\Haven.Core.Client\Haven.Core.Client.csproj`
(a plain net8.0 library, no WinUI/Windows App SDK dependency, holding
`HavenEventClient`/`EventClientCoordinator`) — it restores and builds
automatically as part of the command above.

The event-pipe lifecycle has a real xunit suite (real named pipes, no
WinUI host needed) in `native/Haven.Desktop.Tests`:

```powershell
dotnet test native\Haven.Desktop.Tests\Haven.Desktop.Tests.csproj
```

The local smoke test launches the built WinUI executable against a temporary
real Python Core and verifies named-pipe authentication:

```powershell
python scripts\smoke_native_client.py
```

## Security boundary

The client never receives a direct database path and never writes HAVEN state
itself. Mutations are sent as named IPC methods and are handled by the same
Python application services that enforce owner, scope, confirmation, provider
capability, consequence verification, and ledger recording.
