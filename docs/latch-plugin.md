# Latch plugin (HAVEN)

The Latch page is a read + propose window into [Latch](../../Latch/README.md):
subscriptions (the ledger), access grants under review, the scheduler's
durable review proposals, and the action plans that flow from them. It ships
in two shells that share one boundary:

- **Native (production):** the WinUI desktop page (`native/Haven.Desktop`),
  same navigation rail, `ThemeService` appearance and `Haven*` tokens as the
  rest of the shell. This is the production experience.
- **WebUI (compatibility verification):** the HAVEN static shell page
  (`haven/web/static`), driven through the backend's `/api/latch` adapter
  routes. The Windows App SDK host currently crashes before any native page
  renders, so the WebUI page exists to verify the Latch plugin contracts —
  real records, honest states, the action boundary, handoff — in a real
  browser. It is a compatibility verification surface, not a replacement
  for the native page.

Both surfaces obey the same strict boundary:

> **HAVEN proposes. Latch decides.** Neither page has an authorize,
> execute or verify path. The only mutation that can be triggered is the
> idempotent `actions.plan` proposal; authorization is handed off to Latch's
> own desktop action flow.

**Screenshots** (real records from the fixture-backed daemon, 1440px):
[light theme](screenshots/latch-plugin-light.png) ·
[dark theme](screenshots/latch-plugin-dark.png)

## Startup

Nothing is fabricated: every row comes from a real Latch API response (a
live daemon or its fixture-backed demo), never from constants in HAVEN.

`latchd` is a POSIX daemon: it builds and runs on Linux (and macOS), binds
its HTTP transport to loopback only, and has no native Windows binary. On
Windows hosts, run it in a Linux container (or WSL) and forward the
container's loopback port to the host (e.g. a `socat` sidecar publishing
`0.0.0.0` inside the container network namespace), so both HAVEN and the
handoff deep link see it at `http://127.0.0.1:8787`.

```sh
# 1. From the Latch repository: serve the API *and* Latch's own desktop UI
# on loopback (in a Linux container on Windows hosts):
./build/bin/latchd --socket /tmp/latchd.sock --db ~/.latch/latch.db \
    --http-port 8787 --web-root apps/desktop

# 2. From the HAVEN repository: serve the real WebUI with the Latch adapter:
set HAVEN_DATA_DIR=%LOCALAPPDATA%\Haven.Demo            # scratch data dir
set HAVEN_LATCH_BASE_URL=http://127.0.0.1:8787          # where latchd lives
set HAVEN_LATCH_DEMO=1                                  # Synthetic demo chip
set HAVEN_LATCH_SETTINGS_PATH=%TEMP%\haven-settings.json  # shared appearance
python -m haven.web.server --demo --port 8080

# 3. Open http://127.0.0.1:8080 and select Latch in the navigation rail.
```

(`set` is the Windows shell form; use `export` on POSIX. `--demo` puts the
HAVEN core itself in demo mode, independent of `HAVEN_LATCH_DEMO`, which
only labels the Latch data.) The `--web-root apps/desktop` argument is
required: without a web root latchd serves only the API, and the
authorization handoff has no UI to open.

- Default base URL: `http://127.0.0.1:8787`.
- Override with the `HAVEN_LATCH_BASE_URL` environment variable, e.g.
  `set HAVEN_LATCH_BASE_URL=http://127.0.0.1:8790` — restart HAVEN after
  changing it.
- `HAVEN_LATCH_DEMO=1` labels the data with a **Synthetic demo** chip. It
  changes nothing else: the rows still come from Latch (its synthetic demo
  fixture connectors), and the label exists so demo data is never mistaken
  for a live account.

## The WebUI adapter contract (`/api/latch`)

The WebUI never talks to latchd directly; the browser page calls only the
backend's thin read + propose adapter, which forwards to latchd's loopback
HTTP API. The native page's `LatchClient` and this adapter expose the same
surface — reads, `actions.plan`, and the handoff probe — and neither exposes
authorize/execute/verify:

| Route | Latch upstream | Purpose |
| --- | --- | --- |
| `GET /api/latch/config` | — | `{base_url, demo}` from `HAVEN_LATCH_BASE_URL` / `HAVEN_LATCH_DEMO`. |
| `GET/PUT /api/latch/appearance` | — | Shared appearance persistence (see below). |
| `GET /api/latch/commitments` | `GET /v1/commitments` | Ledger rows. |
| `GET /api/latch/commitments/{id}` | `GET /v1/commitments/{id}` | Full commitment for the inspector. |
| `GET /api/latch/access?stale_only=1` | `GET /v1/access?where={"stale_only":true}` | The grants Latch itself evaluates as stale. |
| `GET /api/latch/schedules/proposals` | `GET /v1/schedules/proposals` | The scheduler's durable review proposals. |
| `GET /api/latch/actions` | `GET /v1/actions` | Action plans (read-only), with optional `commitment_id` / `status` filters. |
| `GET /api/latch/evidence/{id}?content=1` | `GET /v1/evidence/{id}?content=1` | Original evidence records. |
| `POST /api/latch/actions/plan` | `POST /v1/actions/plan` | Idempotent cancellation proposal; returns `{ok, plan}` with the upstream plan record verbatim (wrapped so its `status` field survives the shell's HTTP envelope). |
| `GET /api/latch/handoff?plan_id=…` | probes `GET /` upstream | `{available, url}` for the `?plan_id=` deep link. |

When the adapter or latchd is unreachable the routes return a non-OK status
with an error body; the page renders its disconnected/error states from
that, never from invented data.

## Appearance: one settings file, both shells

The WebUI's Light/Dark/System segmented control (topbar) and the native
Settings radios persist to the **same** settings file the native
`ThemeService` owns: `%LOCALAPPDATA%/Haven.Desktop/settings.json` with
`{theme, mode, density, motion}`. The WebUI PUTs only `mode`; the adapter
rewrites the file, persisting the chosen mode and carrying `theme`,
`density` and `motion` over semantically (JSON values, not byte-for-byte
preservation), and the WebUI has no browser-side theme store (no competing
`localStorage` copy). `HAVEN_LATCH_SETTINGS_PATH` overrides the path for
isolated tests. When the appearance service is unavailable the chosen mode
still applies for the session and the control says its persistence is off;
on load a missing service falls back to System.

The WebUI reuses the shell's existing CSS tokens: the light token block now
applies when the OS prefers light *and* the mode is not pinned to dark, or
when the mode is pinned to light — one palette, three modes, readable in
both.

## What the page shows

Two columns: a list on the left, an inspector on the right. Three tabs,
each keyboard-accessible (`Ctrl+1` Ledger, `Ctrl+2` Access review,
`Ctrl+3` Review proposals; `F5` refreshes; `Tab`/`arrow keys` move between
tabs, lists and actions):

- **Ledger** — commitments from `GET /v1/commitments`, shaped against
  Latch's real record: `economics` (cost), `temporal` (renewal /
  cancel-by), `confidence.overall`, and the backing evidence id array.
  Columns: provider / cost / renewal / cancel-by / source observation /
  confidence. Renewal and cancel-by instants are computed by Latch (its
  temporal rules) and rendered verbatim; HAVEN never computes dates. The
  source-observation column shows each commitment's actual evidence
  (`evidence.get`, metadata only, best-effort after the rows render): the
  connector that observed it and when. Evidence Latch can't serve reads
  **unavailable**; a commitment with no evidence reads `—`. Nothing is
  fabricated.
- **Access review** — the grants Latch itself evaluates as stale
  (`stale_only=true`), so an empty list honestly means **no access
  currently needs review**. Rows show grantee → resource, Latch's own
  `stale_reason`, and the connector observation
  (`source_connector` · `source_object_id` · `updated_at`); a matching
  scheduler proposal adds a **Review proposed** chip. Grants are connector
  observations — they carry no backing evidence record, and the inspector
  says so explicitly instead of inventing one.
- **Review proposals** — the scheduler's durable proposals plus the actual
  action plans (read-only) with their distinct states. Proposals keep their
  nested targets (`access_grant_id` / `commitment_id`) and their evidence
  **array** (source, acquired_at, freshness); where an entry carries an
  `evidence_id`, the inspector loads that record too.

The inspector renders Latch's records verbatim — observed dates
(`created_at`/`updated_at`/`acquired_at`), reasoning (proposal `reason`),
impact (Latch-computed `impact` on action plans), evidence and receipts —
and for commitments and proposals it then loads the **original evidence
records** (with content): connector, acquisition time, content hash and a
bounded content preview. Loads run under a details-selection race token so
fast re-selections never cross-paint, and an evidence record Latch can't
serve is shown as unavailable with Latch's error, never silently dropped or
replaced.

## Action states

Plan states stay distinct end to end:

| Latch state | Shown as | Complete? |
| --- | --- | --- |
| `proposed` | Proposed | no |
| `authorized` | Authorized | no |
| `executed` | Executed | no |
| `needs_verification` | Executed — awaiting verification | **no** |
| `succeeded` / `verified` | Verified | yes |
| `failed` | Failed (explicit) | no |

`needs_verification` means the provider call *reported* success — transport
success is not verification. A plan is only complete when Latch's own
verification confirms the outcome. Action details show the four lifecycle
stages (Proposed → Authorized → Executed → Verified) as a progress display:
transport success parks on *Executed* with *Verified* still pending, and a
failed verification marks *Verified* failed — never complete. Status is
re-read whenever the HAVEN window regains focus or visibility, so a plan
authorized in Latch's flow is reflected when you return to HAVEN.

## The action boundary

- **Propose cancellation** calls only `POST /v1/actions/plan` (idempotent,
  double-submit guarded in the UI). The response is shown with its *actual*
  state: because the plan is idempotent, proposing again can return a plan
  that already advanced (Authorized, Executed, …). Nothing further happens
  in any provider until Latch's own flow advances it.
- **Authorize / Execute / Verify** live exclusively in Latch. The
  **Authorize in Latch…** button opens Latch's real desktop action flow via
  its `?plan_id=` deep link — and only when Latch's own UI is actually
  served: the handoff probe GETs the service root and requires a success
  status *and* an HTML body, so a daemon running without
  `--web-root apps/desktop` (API-only) disables the button with an honest
  *Latch UI unavailable* message instead of opening a 404. The button opens
  a blank window synchronously (so popup blockers accept it), nulls its
  opener, and navigates it to the deep link only after the probe succeeds —
  a blocked initial open is reported honestly and nothing half-opens.

## States and races

The page implements its surfaces honestly and separately: loading, empty
(per tab), disconnected (with startup guidance and a retry), error, and
*stale last-good* — a failed refresh never blanks good data, it labels it
stale with the last successful time. Concurrent refreshes are resolved with
monotonic tokens: every refresh and every details load claims a fresh
sequence number and only the latest renders, so a slow stale response can
never overwrite newer data.

## Configuration reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `HAVEN_LATCH_BASE_URL` | `http://127.0.0.1:8787` | Where the adapter looks for `latchd` (loopback only). |
| `HAVEN_LATCH_DEMO` | unset | `1` shows the **Synthetic demo** chip. |
| `HAVEN_LATCH_SETTINGS_PATH` | `%LOCALAPPDATA%/Haven.Desktop/settings.json` | Overrides the shared appearance settings file (isolated tests). |

## Wired vs acceptance limits

**Wired today (native):** page, navigation item, themed hasp mark, tabs,
ledger / access / proposals rendering against Latch's real record shapes
(via the pure `LatchRecordProjection`), original-evidence loading in the
inspector under a details race token, propose-cancellation via
`actions.plan` with actual-state reporting, authorization handoff gated by
the root-UI probe, all state surfaces, refresh-race protection, keyboard
access, and window-reactivation refresh — all through `LatchClient` (a thin
`System.Net.Http` JSON adapter over the latchd loopback API: reads,
`PlanCancellationAsync`, and `GetAuthorizationHandoffUri`; it exposes no
authorize/execute/verify methods). Contracts are pinned by
`tests/test_latch_plugin.py` (Python, source/XAML) and
`native/Haven.Desktop.Tests` (xUnit: adapter routes/failures, handoff
probe, record-shape projections).

**Wired today (WebUI):** the same page as a HAVEN shell view
(`haven/web/static/{index.html,app.js,styles.css,latch-hasp.svg}`), themed
from the shell's existing CSS tokens with the Light/Dark/System control
persisted through `/api/latch/appearance` to the same settings file the
native `ThemeService` uses; the full/simplified hasp mark from one shared
SVG asset; three tabs over the `/api/latch` adapter routes; honest
loading/empty/stale/disconnected/error surfaces; monotonic refresh-race
tokens; propose-cancellation with actual-state reporting and repeated-click
guard; handoff via `/api/latch/handoff` with honest unavailability; keyboard
access (`Ctrl+1/2/3`, `F5`, arrows); focus/visibility re-read; and the
**Synthetic demo** chip from adapter config. Contracts are pinned by
`tests/test_latch_web_ui.py` (source-level, like the native suite).

**Acceptance:** the WebUI page is verified in production shape — a real
browser against the running HAVEN web server (`python -m haven.web.server
--demo --port 8080`) and a live `latchd` over the landed `/api/latch`
adapter: synthetic demo labeling via `HAVEN_LATCH_DEMO=1`, ledger/access /
proposals rendering, propose-cancellation returning its actual idempotent
state, and the authorization handoff opening Latch's own `?plan_id=` flow.
Source contracts are pinned by `tests/test_latch_web_ui.py`; the adapter's
HTTP contracts by `tests/test_latch_web_api.py`.

**Acceptance still outstanding (native only):** a manual pass of the
running native desktop against a live `latchd` (synthetic demo labeling,
propose → authorize → execute → verify in Latch's own UI, reactivation
refresh) — blocked on the Windows App SDK host crash, which is outside this
plugin. The verified WebUI page covers the same contracts in the meantime.
