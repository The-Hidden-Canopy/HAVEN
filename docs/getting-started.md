# Getting started with HAVEN

This is the shortest path from a new checkout to a useful local HAVEN
installation. You do not need to connect every provider, install a model, or
open Settings before you can start. Begin with the defaults and add one useful
source at a time.

## What you need

For the native Windows experience, have:

- Windows 10 version 1809 or later;
- Python 3.11 or later; and
- the .NET 8 SDK for the first native build.

Home Assistant, a local model, voice hardware, smart-home devices, and cloud
accounts are optional. HAVEN can start as a local computer, files, tasks, and
memory assistant without any of them.

## Install and open HAVEN

From the repository root, install the local package once:

```powershell
py -3 -m pip install -e .
```

If the `py` launcher is not available, use `python -m pip install -e .` with
your Python 3.11+ installation.

For normal use, launch the repository script:

```powershell
.\run-haven.bat
```

You can also double-click `run-haven.bat` in Explorer. The normal native path
starts the HAVEN Core and the WinUI client through a windowless Python host
(`pyw`/`pythonw`), so the launch window can be closed. You do not need to keep
a terminal open. If the launcher reports that `pyw`/`pythonw` is missing,
install the full Python 3 distribution so the windowless launcher is available;
the script will otherwise warn that it must remain attached.

`python -m haven.desktop` is still useful for development. `--web` and
`--debug-web` intentionally keep a development surface attached to the
terminal; they are not the normal user path.

## Complete the first-run setup

The native setup wizard has seven short steps. Optional steps can be skipped
and revisited later from **Setup & connections** in the left rail.

1. **Welcome** — understand what stays local and what is optional.
2. **Storage** — keep the recommended data folder unless you have a reason to
   choose another one.
3. **You** — declare the household owner. This gives HAVEN a clear person who
   can approve important actions once a real provider is connected.
4. **This computer** — optionally connect one or more folders. Reading and
   indexing is read-only by default; file organization is a separate,
   explicitly enabled capability.
5. **Connections** — optionally connect Home Assistant, scan for available
   devices, and enroll only the devices you recognize.
6. **Intelligence & voice** — optionally enable voice and model-backed
   intelligence. HAVEN remains useful without either one.
7. **Ready** — review the choices and enter HAVEN.

The wizard is not a test you have to finish perfectly. A good first run is a
small, honest installation: keep the storage default, declare yourself, and
connect one source you actually want to use.

## Connect things where you need them

You should not have to remember a settings path for ordinary setup:

| What you want to connect | Where to start |
| --- | --- |
| Home Assistant or supported home devices | **Setup & connections** in the left rail, or the connection indicator at the top of the shell |
| A folder for search, recall, and documents | **Computer** → **Connect a folder…** |
| A folder as a memory source | **Memory** → **Connect a folder** |
| Email folders or calendar sources | **Communications** → use the mail or calendar connection action |
| Devices found by local discovery | **Home** → **Discover**, then review and enroll a candidate |
| People, presence sensors, or household context | **People**, or reopen **Setup & connections** |
| Local, downloaded, or external models | **Models**; the optional setup step is only the welcome path |

The connection indicator opens a small **Connection** center with channel
health, retry actions, domain freshness, and a direct **Set up or connect a
provider** action. Settings remains useful for storage, startup, privacy,
diagnostics, and other control-boundary choices; it is not the automatic home
for every connection.

## Your first useful session

After setup, use this loop:

1. Open **Today** and read the temporal overview.
2. Open **Needs You** when it has something waiting. Review the evidence and
   the requested action before approving, confirming, or dismissing it.
3. Use **Projects** and **Tasks** for commitments you want to keep visible.
4. Use **Home**, **Computer**, or **Communications** to inspect the source
   behind a signal or connect the next useful source inline.
5. Return to **Today** and check that the consequence or new information is
   reflected there.

If a provider is unavailable, stale, or still reconnecting, HAVEN should show
that state rather than quietly presenting an empty world. Give an unfamiliar
device or source the same review before enrolling or enabling it.

## If something goes wrong

First use the visible **Retry**, **Reconnect**, or **Refresh** action on the
affected page. The shell also exposes connection health from the connection
indicator. If the native client needs a clean restart, close HAVEN and launch
`run-haven.bat` again; the Core owns the persistent state and the client can be
reopened.

For a default installation, durable host logs are in `%USERPROFILE%\.haven`:
`haven.log` contains structured host logging and `haven-console.log` captures
console-style output when no console is attached. If you chose another data
folder, use that folder instead.

## Thank you

Thank you for trying HAVEN. Start with one useful connection, keep the signals
honest, and have fun making it yours.
