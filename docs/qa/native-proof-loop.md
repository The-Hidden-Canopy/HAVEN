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

## Artifact checklist

For each run, record:

- [ ] Build identity: git commit, `dotnet build` output (0 warnings/errors).
- [ ] A screenshot or screen recording per step above.
- [ ] The native diagnostic export (Settings → System → Export diagnostics,
      once the diagnostic-export backlog item lands) taken after step 9 and
      again after step 10.
- [ ] Any defect found, filed with: which step, expected vs. actual, and
      whether a WebUI fallback was needed (a hard fail on its own).

## Known standing gaps (do not treat as new defects)

- WinUI compositing is intermittent on some dev machines (documented
  elsewhere in `TASKS.md`); if the window renders black, that is an
  environment issue to route around (different machine/session), not a
  product defect to file here.
- Conversation providers (Slack/Teams-style), the encrypted/P2P sync
  transport, and full tablet touch QA are explicitly out of scope for this
  loop — they have their own backlog items.
