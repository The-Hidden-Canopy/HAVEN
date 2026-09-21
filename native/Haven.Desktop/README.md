# HAVEN Desktop

This is the native Windows client boundary for HAVEN. It is intentionally a
thin WinUI 3 client: authority, providers, persistence, knowledge, search, and
speech remain in the Python Core.

The client speaks `haven-ipc-1` over a local Windows named pipe. The Python
desktop launcher starts the Core and passes the pipe name plus a one-time
bearer token through an inherited stdin pipe; the token is not placed in the
native process command line. Manual client launches may use the equivalent
`HAVEN_IPC_PIPE` and `HAVEN_IPC_TOKEN` environment variables.

In native mode the compatibility HTTP socket is closed after the Python
service graph is composed; the native client communicates only through the
local named pipe. Browser mode continues to expose the existing loopback WebUI
when explicitly launched without `--native`.

The Python desktop launcher can start this client with `--native`:

```powershell
python -m haven.desktop --native
```

Without that flag, the compatibility Edge/WebUI host remains available while
the native surface grows toward feature parity. The native client currently
proves the Core connection, state read, life search, read-only memory/evidence
inspection, and composer request paths; it does not yet replace every setup,
home, and settings view.

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
