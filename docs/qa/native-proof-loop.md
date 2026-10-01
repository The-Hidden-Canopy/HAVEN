# Native operational proof loop

Source: `HAVEN_Next_Phase_Engineering_Plan.docx` §3 (Priority 0). This is a
hands-on, interactive script — no substitute for it exists in the automated
suites. The plan's own framing: "the code is ahead of hands-on validation.
Product risk now concentrates in runtime lifecycle, focus, dialogs, event
reconnection, persistence and recovery." Passing every unit/contract test in
this repo proves a route exists; it does not prove a person can complete it.

Run this after any change that touches setup, the event channel, persistence,
or backup/restore, and before any release tag (per §10.3's "no release tag if
native smoke flow fails, even if unit suites are green").

## Prerequisites

- A Windows account with no prior `%LOCALAPPDATA%\Haven.Desktop` /
  HAVEN data directory (or point `HAVEN_DATA_DIR` at a fresh empty folder).
- `dotnet build -p:Platform=x64` green for `native/Haven.Desktop`.
- No WebUI fallback used anywhere in this script — if a step requires
  `--web`/`--debug-web` to complete, that is a fresh-install defect, not a
  workaround.

## Scenario

Run these in order; each step's evidence is what to capture (screenshot,
diagnostic export, or log excerpt) when filing a defect against it.

1. **Fresh launch.** `run-haven.bat` (or the built `Haven.Desktop.exe`
   directly) on the clean account. Expect: splash, then the native setup
   wizard — never a WebUI page.
2. **Setup wizard.** Complete every step natively: data directory, provider
   connection (or skip/demo), discovery/enroll, preferences, computer roots.
   Expect: no step silently no-ops; every `ok:false` envelope surfaces
   in-line (per the interaction rules in the engineering plan §8.1).
3. **Household authoring.** Declare the owner, then at least one additional
   person or context via People.
4. **Rooms.** Create at least two rooms manually via Home → Rooms → Add room.
5. **Discovery.** Run a real local scan (Home → Discover) or connect one
   provider (e.g. Home Assistant); review a candidate and enroll it.
6. **Automation lifecycle.** Create a governed automation, edit it while
   still `proposed`, approve it, then enable it. Expect: the proposed→edit→
   approve→enable transitions each show a visible result (spec §8.1's "every
   mutation has a visible result").
7. **Governed confirmation.** Execute a device or resource action that
   requires confirmation (e.g. a garage close, or a file move if Computer
   access is enabled) and complete the confirmation natively.
8. **Cross-surface object.** Create a project and a task; verify both surface
   in Today (Focus/Needs You/Upcoming/Tasks as applicable) and in Search.
9. **Abrupt close + restart.** Kill the process (not a graceful quit).
   Relaunch. Verify household declarations, rules, provider state, claims,
   and every UI projection from steps 3–8 survived.
10. **Event-channel stall.** With the app running, block or kill the events
    pipe out from under it (e.g. a firewall rule, or a debugger break in
    `HavenEventClient`) while the RPC channel stays healthy. Verify: the
    Settings channel-health rows (`EventChannelStatusText`) show "Stalled"
    with a last-frame time, then "Reconnecting…", then "Connected" once
    restored — no duplicated state transitions, no frozen page.
11. **Provider unavailable at restart.** Restart with a configured provider
    unreachable (e.g. Home Assistant stopped). Verify Today/Home show a
    distinct "unavailable" state, never an empty list masquerading as "no
    data" (spec's "false emptiness" anti-pattern).
12. **Backup restore.** Create a backup (Settings → System), restore it into
    a clean data directory, and verify the restart-required boundary is
    honest (the app tells you to restart, not silently half-applies state).

## Latest local validation record

This record is a disposable Windows run against a fresh `HAVEN_DATA_DIR`.
It is evidence for the items below, not a claim that the complete twelve-step
acceptance loop is closed.

- **Steps 1–2 — pass.** The built native client launched as `HAVEN` and the
  seven-step setup wizard was completed entirely through the native UI. The
  run used the default storage location, skipped optional provider setup, and
  invoked the real local discovery scan; no WebUI fallback was used.
- **Step 3 — partial.** An owner was declared and persisted. No additional
  person or context was added in this run.
- **Step 4 — pass.** Two rooms were added manually from Home → Rooms and
  remained present after a supported core-plus-native restart.
- **Step 5 — partial.** The native discovery scan completed with no candidates
  in the disposable environment, so no device was enrolled.
- **Step 6 — blocked by an explicit product precondition.** Propose
  automation surfaced the native `Connect a device first` result because the
  run had no writable device capability. No automation lifecycle claim is
  made.
- **Step 7 — not exercised.** There was no enrolled device or enabled
  computer root available for a governed confirmation.
- **Step 8 — pass.** A project and dated task were created natively. The task
  appeared in Today, and native Search returned the task and project by title.
- **Step 9 — pass for the supported launcher path.** After an abrupt native
  close, restarting the core plus native shell restored Connected state, setup
  completion, rooms, the Today task, and Search results. Restarting only the
  native shell correctly showed the core as unavailable and is not counted as
  the supported restart path.
- **Steps 10–12 — not exercised.** Event-channel stall/recovery, an
  unavailable configured provider, and backup/restore still require separate
  hands-on runs.

### Supplemental launch check — 2026-10-01

A second disposable fresh-data launch was started with the native client and
no `--web` fallback. Core initialization created the expected local stores and
the native process exposed a `HAVEN` window, but the available Windows
automation surface returned no targetable app windows (`apps: []`). No click,
typing, screenshot, or proof-loop step was therefore claimed from this
attempt; the disposable process was stopped after observation. A manual run on
a host with a targetable WinUI surface is still required for the interactive
steps and artifact checklist below.

### Repeated targetability check — 2026-10-01

The current native build was launched again against the disposable
`data/native-proof-20261001` directory with no `--web` fallback. Process-level
inspection confirmed a responsive `HAVEN` window, while the available Windows
computer-use surface again returned `apps: []` and no targetable window. No
click, typing, screenshot, or proof-loop step was claimed; the explicitly
identified disposable core/native processes were stopped after observation.
This repeats the host/tooling limitation above and does not constitute a
product acceptance result.

### Third targetability check — 2026-10-01

A third disposable launch used a writable fresh data directory and the
current native build. Process inspection again confirmed a responsive
`HAVEN` window, while the available Windows computer-use surface returned
`apps: []`. No interaction or screenshot was claimed; the verified launcher
and native processes were stopped and the disposable data directory removed.
The interactive proof loop therefore remains unverified on this host.

### Fourth targetability check — 2026-10-01

A fresh native launch was first attempted detached and then repeated attached
with the prebuilt client path quoted correctly. The attached run created the
expected fresh local stores; process inspection showed a responsive
`Haven.Desktop.exe` window titled `HAVEN`. The available Windows computer-use
surface still returned `apps: []`, so it exposed no targetable window,
accessibility tree, or screenshot-backed action target. No UI action or proof
step was claimed. The Python/native processes and both disposable data
directories were stopped and removed after observation.

The evidence above was captured as live UI-automation observations; no
screenshots or diagnostic exports were produced by this run. The full artifact
checklist below remains required before a release tag.

### Verification refresh — 2026-10-01

The current local continuation was verified in two bounded environments:

- The current full Python suite collected **1840 tests** and completed
  successfully when run with a writable external temp root. A run that placed
  pytest's temp root inside the OneDrive checkout hit the known filesystem ACL
  boundary during atomic replacement; the isolated rerun passed the affected
  automation, diagnostics, and authoring cases. No weaker credential or
  provider fallback was introduced.
- A disposable writable clone ran the native project references and
  `Haven.Desktop.Tests`: **6/6 passed**. The run emitted only the existing
  offline NuGet vulnerability-feed warning and missing publish-profile warning.

These results confirm local code/test coverage without converting the host-only
limitations into product acceptance claims. The interactive proof loop and
target-hardware/provider gates remain open until they can be run on a suitable
Windows session.

### Discovery verification refresh — 2026-10-01

The continued build-spec slice added a durable harmless-read boundary to the
everyday Discover flow. The focused discovery, IPC, and sidecar-migration
selection passed **38 tests**. It covers an observed provider state becoming
`verified`, an empty/provider-failure result remaining `unavailable`, unknown
device refusal, sidecar persistence, and reload. The related automation and
External Agent/MCP regression selection also passed. A disposable writable
native clone built the changed client and ran `Haven.Desktop.Tests` **6/6
passed**; only the known offline NuGet vulnerability-feed and missing
publish-profile warnings were emitted. This is code/test evidence, not a
claim that real target hardware or the interactive native proof loop has been
completed.

The same refresh now exposes discovery transport readiness in the scan envelope
and native Discover surface: Wi-Fi/SSDP and Wi-Fi/mDNS are reported as
standard-library transports, while an unavailable native Bluetooth library is
reported with an explicit unavailable state rather than being described as a
successful Bluetooth scan. This is a truthful transport boundary, not evidence
of a nearby device or of WinRT DLL installation; those remain external gates.
The transport-readiness follow-up expanded the focused discovery/IPC/native
contract selection to **42 tests**, all passing.

### Email mutation verification refresh — 2026-10-01

The remaining credentialed-email mutate gap is now closed locally: the IMAP
provider resolves one stable `Message-ID`, marks it `\\Deleted`, expunges it,
and searches again to verify absence. Core, IPC, HTTP, native UI, and resource
automation options all expose the same confirmation-gated action; local `.eml`
maildirs continue to report read-only capabilities. The focused provider/IPC/
automation/native-contract selection passed, and the HTTP contract preserves
the `confirmation_required` boundary. Live mailbox credentials and network
acceptance remain external gates.

## Artifact checklist

For each run, record:

- [ ] Build identity: git commit, `dotnet build` output (0 warnings/errors).
- [ ] A screenshot or screen recording per step above.
- [ ] The native diagnostic export (Settings → System → Export diagnostics,
      now implemented) taken after step 9 and again after step 10.
- [ ] Any defect found, filed with: which step, expected vs. actual, and
      whether a WebUI fallback was needed (a hard fail on its own).

## Known standing gaps (do not treat as new defects)

- WinUI compositing is intermittent on some dev machines (documented
  elsewhere in `TASKS.md`); if the window renders black, that is an
  environment issue to route around (different machine/session), not a
  product defect to file here.
- Conversation providers (Slack/Teams-style), hosted/P2P sync relay, and full
  tablet touch QA are explicitly out of scope for this loop — the encrypted
  folder transport has its own automated coverage, and the remaining items
  have their own backlog entries.
