# Milestone A — Native default

Source: `HAVEN_Native_Life_Assistant_Design_Spec_v1_0.docx` v1.0 (2026-09-22), page 44 milestone table.

**Exit condition:** HAVEN launches WinUI by default; WebUI is explicit debug only.

## Tasks

- [x] Flip the CLI default in `haven/desktop/shell.py:main()`: replaced `--native` (opt-in, default `False`) with `--web`/`--debug-web` (opt-*out*, `dest="web"`); `DesktopShell(..., native=not args.web, ...)` now defaults to native when no flag is given.
- [x] ~~Flip the matching default in `DesktopShell.__init__`~~ — scoped out. `main()` always passes an explicit `native=` value, so the class-level `native: bool = False` default never affects end-user launch behavior. Every existing direct caller that omits `native=` (most of `tests/test_desktop_shell.py`, plus setup/activation tests) is deliberately exercising the Edge-specific path, and `scripts/smoke_native_client.py` already passes `native=True` explicitly. Flipping the class default would only churn ~10 test call sites for no behavior change at the CLI, so it was left as-is.
- [x] Update `tests/test_desktop_shell.py` — covered by `test_main_launches_native_by_default` (asserts `main([])` passes `native=True` to `DesktopShell`) and `test_main_web_flag_falls_back_to_the_compatibility_host` (asserts `--web`/`--debug-web` produce `native=False`), plus the two banner tests.
- [x] Update the "native client is not built" error in `_resolve_native()` so the first-run failure message tells the user how to fall back to `--web`/`--debug-web`.
- [x] Update the Edge-path error in `_resolve_edge()` to reference `--web`/`--debug-web` instead of the old always-available WebUI framing.
- [x] `run-haven.bat` needs no change (forwards `%*` to `haven.desktop` unchanged) — plain `run-haven.bat` now opens the native client, `run-haven.bat --web` is the debug path. No doc comment added to the .bat itself (it has no comments today); noted here instead.
- [x] Swept `README.md`'s "Run the surface" section (previously assumed Edge/WebUI was default) and `native/Haven.Desktop/README.md` — both now describe native as default and `--web`/`--debug-web` as the explicit fallback.
- [x] Added a startup banner: `haven/desktop/shell.py:main()` prints a stderr note when `--web`/`--debug-web` is passed ("development/debug only; the native WinUI client is the production experience"), and `haven/web/server.py:main()`'s own listening banner carries the same "development/compatibility only" language for the standalone `python -m haven.web.server` entry point. Native launches print nothing extra. Covered by `tests/test_desktop_shell.py::test_main_web_flag_prints_a_debug_surface_banner` / `test_main_native_default_prints_no_web_banner`.

## Milestone A — done

All tasks above are complete and verified: `tests/test_desktop_shell.py`, `tests/test_web_knowledge.py`, and `tests/test_ipc_server_adapter.py` pass (25/25). The exit condition holds — `python -m haven.desktop` / `run-haven.bat` now launch the native WinUI client by default, and `--web`/`--debug-web` is the explicit, clearly-labeled debug fallback. Remaining pre-existing failures elsewhere in the suite (`test_claim_admission.py` `NameError: Claim`, `test_plugins_*`/`test_web_plugins.py` `PermissionError`) are unrelated to this milestone — they belong to separate in-progress work.

# Milestone B — Native parity

**Exit condition:** Normal user no longer needs WebUI.

**Current native surface** (`haven/web/server.py:build_ipc_dispatcher`, ~line 447): only 15 read-mostly IPC methods exist — `host.capabilities`, `state.get`, `setup.status`, `models.overview`, `search.query`, `knowledge.claims`/`claim`/`claim.correct`/`claim.stale`/`claim.forget`, `composer.ask`, `computer.action.request`/`confirm`/`deny`/`history`. The native client itself (`native/Haven.Desktop/`) is just `App`, `MainWindow`, and one `HavenCoreClient` service — no per-domain views exist yet.

**What the WebUI actually covers today** (`haven/web/static/app.js`, 4758 lines) that native parity needs to match, grouped by REST surface actually found in the file:

- **Setup wizard** (largest single piece — welcome → data-dir → provider → discovery/enroll → household people/contexts → preferences → computer roots → finish → reopen): `/api/setup`, `/api/setup/data-dir`, `/api/setup/provider`, `/api/setup/enroll`, `/api/setup/discovery/scan`, `/api/setup/household/people[/remove]`, `/api/setup/household/contexts[/remove]`, `/api/setup/preferences`, `/api/setup/computer[/roots[/remove], /scan]`, `/api/setup/complete`, `/api/setup/reopen`, `/api/setup/providers/packages[/install,/enable,/uninstall]`.
- **Rooms & devices**: `/api/rooms[/{id}]`, `/api/devices/{id}` (command dispatch).
- **People & contexts** (outside setup — ongoing authoring): `/api/people[/{id}]`, `/api/contexts[/{id}]`.
- **Automations**: `/api/automations/options`, `/api/automations[/{id}]` (create/edit/enable/disable/approve/revoke).
- **Models**: `/api/models`, `/api/models/assign`, `/api/models/{action}`, `/api/models/register`, `/api/models/jobs[/{id}/cancel]`, `/api/models/events`, `/api/models/download`, `/api/models/inspect`, `/api/models/install-local`, `/api/models/add-root`, `/api/models/scan`, `/api/models/add-endpoint`.
- **System**: `/api/system/diagnostics[/probe]`, `/api/system/backups`, `/api/system/backup[/restore,/delete]`, `/api/system/service[/{id}]`.
- Out of scope for B (belong to other milestones): voice (`/api/voice/*`, page 36), plugins/extensions (`/api/plugins/*`, milestone I taxonomy), chat/demo endpoints (dev-only).

## Sub-phases (pick one to start — each is independently shippable toward the exit condition)

- [ ] **B1 — Setup wizard parity.** Native WinUI flow covering data-dir, provider, discovery/enroll, household people/contexts, preferences, computer roots, finish/reopen. Hard prerequisite: without this, a fresh install still needs the WebUI at least once.
  - [x] **B1a — IPC backend.** Added 19 `setup.*` methods to `build_ipc_dispatcher()` (`haven/web/server.py`), delegating to the same `self.setup` (`SetupService`) instance the WebUI uses — no forked validation: `setup.data_dir`, `setup.provider.connect`, `setup.enroll`, `setup.discovery.scan`, `setup.household.people.add`/`.remove`, `setup.household.contexts.add`/`.remove`, `setup.preferences`, `setup.computer`, `setup.computer.roots.add`/`.remove`, `setup.computer.scan`, `setup.complete`, `setup.reopen`, `setup.providers.packages`/`.install`/`.enable`/`.uninstall`. Covered by 3 new tests in `tests/test_ipc_server_adapter.py` (household people/contexts round-trip, preferences + provider-packages listing + complete/reopen lifecycle). Full related suite green (`test_ipc_server_adapter`, `test_desktop_shell`, `test_web_knowledge`, `test_web_setup`, `test_setup_*`).
  - [x] **B1b — WinUI setup wizard views.** Native setup wizard mirroring `app.js`'s `renderSetup*` flow: `Setup/SetupWizardView.xaml(.cs)` — a 7-step wizard (welcome → storage/data-dir → you/household people+contexts+presence sensors → this computer/roots+scan → connections/Home Assistant connect + discovery/enroll → intelligence & voice → ready) shown as an overlay on `MainWindow` whenever `setup.status` reports setup incomplete, with a header "Setup" button calling `setup.reopen` once complete. 18 typed setup methods added to `Services/HavenCoreClient.cs` (all 19 B1a methods except the `setup.providers.*` packages group, which no wizard step needs yet); every step surfaces the core's `ok:false`/`error` envelopes in-line; FolderPicker (with `InitializeWithWindow`) backs data-dir and computer-root selection; the scan responses refresh `setup.status` so candidates/roots render immediately. Build green (`dotnet build -p:Platform=x64`, 0 warnings); Python contract suites green (`test_ipc_server_adapter`, `test_web_setup`, `test_desktop_shell`, `test_setup_*`). Interactive click-through not run (headless); provider-connect/enroll paths exercised only via the Python-side tests.
- [ ] **B2 — Rooms & devices view.** Room list/detail, device command dispatch (`native/Haven.Desktop` already has no equivalent of `app.js`'s `renderRoomsView`/`makeDeviceRow`).
- [ ] **B3 — People & contexts view.** Ongoing (post-setup) CRUD, distinct from the setup-wizard's household authoring step.
- [ ] **B4 — Automations authoring.** Draft/approve/revoke lifecycle per spec page 34; native equivalent of `renderAutomations`/`authoringButton` dialogs.
- [ ] **B5 — Models management.** Full Model Manager parity (register/download/inspect/jobs) beyond the existing read-only `models.overview`.
- [ ] **B6 — System view.** Diagnostics, backups, service control.

Each sub-phase needs: (a) new `*.py` handlers added to `build_ipc_dispatcher()` delegating to the *same* application services the WebUI already uses (no forked validation, per spec page 17's "native and web adapters must not fork validation rules"), (b) a corresponding WinUI page/view, (c) tests mirroring the existing `test_web_*` coverage for the same service.

## Deferred to later milestones (not required for A's exit condition)

- MSIX/installer packaging, `HAVEN.exe`/`haven-core.exe` split — Milestone A's repo-change column mentions "packaging" but the spec's own packaging detail (page 42) is scoped separately; don't block A on it.
- Renaming `run-haven.bat` itself or building a signed installer.

# Milestone C — Personal scope

Source: spec pages 18–19 (personal root scope, identity, scope graph).

**Exit condition:** Search/resources no longer hard-bound to household scope.

- [ ] Implement `LocalIdentityProvider` over the existing identity contract; stable `principal_id` + `personal_scope_id` on first run.
- [ ] `ScopeStore` with `ScopeRef`/`Membership` records (personal | project | workspace | household | shared | custom); visible scopes derived from authenticated principal memberships, not caller-supplied lists.
- [ ] Migration: existing installations keep their household identifier as a legacy child scope; personal root becomes parent; computer resources/claims migrate to personal scope unless explicitly home-specific; auditable legacy-scope mapping so historical receipts stay interpretable.
- [ ] De-household the dispatch/search path: `build_ipc_dispatcher` and the search/resource queries currently constrain visibility to the current household scope — re-derive from principal memberships.
- [ ] Tests mirroring spec page 43's authority gates: cross-scope denial, role/capability denial, unauthorized objects invisible *including through relationship expansion paths*.

# Milestone D — Projects + Tasks

Source: spec pages 26–27 (projects domain, objective graph), page 11 donors (BuildThread revision-bound proposals, NOMAD degradation).

**Exit condition:** First complete non-home workflow (create project → attach file → task with dependency → complete with evidence).

- [ ] `haven/domains/projects` + `haven/domains/tasks` packages with `ProjectRecord`/`TaskRecord` minimum fields from spec pages 26–27 (revision, provenance, task states PROPOSED/OPEN/IN_PROGRESS/BLOCKED/DONE/CANCELLED).
- [ ] Application services (`haven/application`) hosting the new domains; HTTP server grows only a thin adapter — no new business logic under `haven/web` (spec page 17 placement rule).
- [ ] Project store: archive/tombstone default on delete; attachment = relationship assertion, never moves/copies the source object.
- [ ] Task behavior: model-derived next steps stay PROPOSED until accepted; recurrence via deterministic scheduler; revision-bound mutations so stale suggestions can't overwrite newer edits; completion keeps its evidence source (user-declared vs provider-observed).
- [ ] Native ProjectsPage/TasksPage (mockups: gallery view, project detail with progress/key dates/activity, task list with priority/project/due) wired over IPC `projects.*`/`tasks.*`.
- [ ] Projection: domain events → `ResourceStore`/`OntologyStore`/search indexes via a projection coordinator, so searchable records never become accidental authorities.
- [ ] IPC method families `projects.*`, `tasks.*` + `project.changed`/`task.changed` events.

# Milestone E — Computer context

Source: spec pages 29–30 (files/document ingestion, applications & window awareness).

**Exit condition:** HAVEN can resume local work without screen scraping.

- [ ] Filesystem provider stays read-only default; document pipeline already partially exists — extend ingestion extractors in spec order: DOCX, PDF, HTML, structured JSON/YAML/TOML, CSV (page 22), behind optional adapters so stdlib core keeps working without a format plugin.
- [ ] Application/window provider: top-level window enumeration + process identity (no keylog, no screenshots/UIA by default); foreground-change observation opt-in with per-app suppression; `ApplicationResource`/`WindowResource` projections.
- [ ] Recent-work projection ("what was I editing yesterday?") from window/app events + filesystem activity.
- [ ] Safe action set per page 29: Reveal/Open automatic; rename/move/copy governed with verification receipt; launch executable as separate high-risk capability; delete excluded.
- [ ] Native Computer lens + document detail pane (claims/relationships, no filesystem privilege in the UI layer).

# Milestone F — Browser + Comms

Source: spec pages 31–32 (browser tabs as resources; calendar/email/conversations).

**Exit condition:** Cross-system life search and project correlation.

- [ ] `BrowserTabResource` provider (tab identity, URL/title/activity, stale-on-close, no body/cookies/history indexing by default, incognito excluded); actions focus/open/close with confirmation rules from page 31.
- [ ] Calendar provider: `CalendarEventResource` projection; create/update/delete via provider with re-fetch verification; events attachable to projects and able to *propose* tasks without auto-creating.
- [ ] Email provider: `EmailResource` (sender/recipients/subject/time/thread/labels/bounded snippet); full-body indexing opt-in and scope-aware; send/delete are confirmation-gated provider actions; "waiting on" stays a proposal.
- [ ] Conversation provider shape (Slack/Teams-style connectors follow the same contract).
- [ ] Read access and send/mutate access are separate provider capabilities end-to-end (page 32 decision).

# Milestone G — Today + graph

Source: spec pages 21, 25 (ontology/relationship generation; attention layer).

**Exit condition:** Explainable "what matters now" with per-card reasons.

- [ ] Learned-relationship pipeline: observation/text/model output → `CandidateRelationship` (evidence refs + confidence + predicate) → `RelationshipAdmissionPolicy` → admitted or needs-review (page 21). A model may suggest an edge; it may not silently create durable high-impact relationships.
- [ ] Affected-region recomputation (BuildThread wake-set): a changed resource recalculates only connected graph regions with bounded expansion.
- [ ] Attention projection (`Today`): candidate signals from page 25, card contract (title/why now/scope/evidence/next action), ranking rules — hard deadlines/commitments/pending authority before speculation; suggested work becomes a task only after explicit acceptance.
- [ ] Native Today dashboard per mockup: Focus card, Upcoming, Tasks, Recent files, Pending replies.

# Milestone H — Sync/tablet

Source: spec pages 19, 40, 42 (scope sharing, sync, tablet strategy).

**Exit condition:** One personal state across native devices.

- [ ] Sync event envelope: `origin_device_id`, `object_id`, revision/causal parents, `scope_id`, `event_id`.
- [ ] Sync policy: user-authored projects/tasks/people/claims sync; raw file content, model weights, provider credentials, home secrets do not sync by default.
- [ ] Conflict state requiring merge/review — never silent last-write-wins on high-value records; provider-return recovery reconciles rather than assumes.
- [ ] Transport behind an interface (peer-to-peer or encrypted relay; Hub relay, if used, is never authority).
- [ ] Tablet-adaptive layouts: same WinUI app, touch targets, NavigationView/master-detail collapse (page 42 phase 1).

# Milestone I — Extensions

Source: spec pages 12, 41 (extension taxonomy, business model).

**Exit condition:** Paid/free ecosystem without authority leakage.

- [ ] Taxonomy: Providers, Intelligence Services, Feature Modules, Export Consumers — each with declared run-location and access/authority boundaries.
- [ ] Reclassify the current `haven/plugins` receipt-consumer surface as Export Consumers in UI/docs until the broader taxonomy lands.
- [ ] Intelligence-service boundary: bounded context in, proposals/answers out, never authority-bearing mutations; paid services get no broader data access than free local models.
- [ ] Feature-module contract: native/domain extensions must call application services, not stores directly.

# Definition of done (spec page 44, verbatim target)

HAVEN is "native life assistant v1" when a fresh user can install it, create projects/tasks/people, point it at allowed files, search and correlate their information, inspect/correct memory, use local or external models, perform governed computer/home actions, and close/reopen the native app without touching a browser surface — while every action remains scoped, explainable, and independently verifiable where the provider permits it.

# Repo baseline survey (branch main @ 83672f6)

Facts below ground the milestone notes above; revisit this section when a milestone starts.

- **Identity/scopes (C).** Identity is `Principal(actor_id, household_id, role_tier)` — there is no `principal_id` concept yet (`haven/core/domain.py:198`). `haven/identity/contracts.py` has the `IdentityProvider` Protocol + `ScopeMembership` but explicitly no local implementation; `haven/scopes/models.py` has `ScopeRef(scope_id, kind, name, parent_scope_id)` — contract only, no registry/persistence. `household_id` is the de-facto isolation scope, hard-rejected otherwise at `haven/web/server.py:265-278` (`_visible_scope_ids` allows only the authenticated household) and again at server.py:702-731; it appears 323× across 28 files. Resources/claims/ontology are already keyed by generic `scope_id` — the plumbing upgrade is real but bounded. People exist only as setup declarations, not records.
- **Domains (D).** No `haven/domains` or `haven/application` package; no Project/Task/Person records. Ontology predicates (`assigned_to`, `depends_on`, … `haven/ontology/predicates.py:9-10`) and the `ScopeRef` docstring anticipate them.
- **Computer (E).** `haven/integrations/computer/filesystem.py` covers allowed-roots scanning, symlink re-checks, read-only mode, and a governed action set (`filesystem.open/reveal/create_folder/copy/move/rename` via the risk table in `haven/web/computer_actions.py:68-76` and `ResourceAuthorityEngine`). **No application/window awareness, no browser provider.** Extraction is plain-text only: `TEXT_SUFFIXES = {.txt, .md, .markdown, .rst}` (`haven/knowledge/extraction/content.py:11-15`) — the DOCX/PDF/HTML/structured/CSV extractors from spec page 22 are all greenfield, and content reads deliberately go through the provider boundary.
- **Comms (F).** Nothing exists — no calendar/email/conversation providers or contracts; only forward references in comments (`haven/actions/models.py:9`). Greenfield.
- **Models (B5).** `haven/models/` is substantial and working: full ModelManager lifecycle (search/inspect/install/register/assign/load/unload, `manager.py:195-610`), hash-verified downloads, persisted job store with SSE, web API at `haven/web/models_api.py`. Native parity is mostly view + IPC surface work, not new core.
- **Ontology (G).** `OntologyStore` (SQLite, indexed `edges_from`/`edges_to`) exists but is **never fed** — no relationship generation in production; search's ontology expansion (`haven/search/service.py:167-170`) would consume edges if any existed.
- **Sync (H).** `haven/sync/contracts.py` (`SyncRecord`/`SyncBatch`/`SyncProvider`) only; no implementation, no references elsewhere.
- **Plugins (I).** `haven/plugins/` is already the narrow receipt-consumer boundary (plugins never run inside HAVEN, never get live state — `docs/plugin-boundary.md`). A separate provider-plugin mechanism exists (`haven/providers/plugin.py`, `ProviderManifest` Protocol, loaded via `setup.providers.install`). Milestone I is taxonomy + surfaces, not new isolation machinery.
- **IPC (affects B–G).** The named-pipe dispatcher (`haven/ipc/dispatcher.py`) is strictly request/response — **no event push**. The spec's recommended events (`state.changed`, `project.changed`, `action.progress`, … page 16) need an event channel added; web SSE exists only browser-side (`/events`, `/api/models/events`) with naive `retry: 3000` reconnect, no resume cursor. B2–B6 views will need polling or a new IPC event stream.
- **Tests.** 125 files / 1258 tests, green at HEAD. The earlier `test_claim_admission.py`/`test_plugins_*` failures were environmental (broken ACLs on a stale pytest temp dir), not code. `tests/test_web_plugins.py` is flaky in full-suite runs (live-network catalog tests, `WinError 10053` while booting a real server); each failure passes in isolation.
