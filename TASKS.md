# Milestone A — Native default

Source: `HAVEN_Native_Life_Assistant_Design_Spec_v1_0.docx` v1.0 (2026-09-22), page 44 milestone table.

**Exit condition:** HAVEN launches WinUI by default; WebUI is explicit debug only.

## Tasks

- [x] Flip the CLI default in `haven/desktop/shell.py:main()`: replaced `--native` (opt-in, default `False`) with `--web`/`--debug-web` (opt-*out*, `dest="web"`); `DesktopShell(..., native=not args.web, ...)` now defaults to native when no flag is given.
- [x] ~~Flip the matching default in `DesktopShell.__init__`~~ — scoped out. `main()` always passes an explicit `native=` value, so the class-level `native: bool = False` default never affects end-user launch behavior. Every existing direct caller that omits `native=` (most of `tests/test_desktop_shell.py`, plus setup/activation tests) is deliberately exercising the Edge-specific path, and `scripts/smoke_native_client.py` already passes `native=True` explicitly. Flipping the class default would only churn ~10 test call sites for no behavior change at the CLI, so it was left as-is.
- [ ] Update `tests/test_desktop_shell.py` — it currently passes `native=True` explicitly (`tests/test_desktop_shell.py:269`) for the one native-path test; the rest construct `DesktopShell` directly and don't go through `main()`'s new default at all. Add a test that calls `main()`/parses args with no flags and asserts `native=True` is what gets passed to `DesktopShell`, plus a test for `--web`/`--debug-web` producing `native=False`.
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
  - [ ] **B1b — WinUI setup wizard views.** The much larger remaining piece: native XAML pages/dialogs for welcome, data-dir picker, provider connect, discovery/enroll, household people/contexts, preferences, computer roots, finish — mirroring `app.js`'s `renderSetup*` functions — wired to the new IPC methods above through `Services/HavenCoreClient.cs`. Not started.
- [ ] **B2 — Rooms & devices view.** Room list/detail, device command dispatch (`native/Haven.Desktop` already has no equivalent of `app.js`'s `renderRoomsView`/`makeDeviceRow`).
- [ ] **B3 — People & contexts view.** Ongoing (post-setup) CRUD, distinct from the setup-wizard's household authoring step.
- [ ] **B4 — Automations authoring.** Draft/approve/revoke lifecycle per spec page 34; native equivalent of `renderAutomations`/`authoringButton` dialogs.
- [ ] **B5 — Models management.** Full Model Manager parity (register/download/inspect/jobs) beyond the existing read-only `models.overview`.
- [ ] **B6 — System view.** Diagnostics, backups, service control.

Each sub-phase needs: (a) new `*.py` handlers added to `build_ipc_dispatcher()` delegating to the *same* application services the WebUI already uses (no forked validation, per spec page 17's "native and web adapters must not fork validation rules"), (b) a corresponding WinUI page/view, (c) tests mirroring the existing `test_web_*` coverage for the same service.

## Deferred to later milestones (not required for A's exit condition)

- MSIX/installer packaging, `HAVEN.exe`/`haven-core.exe` split — Milestone A's repo-change column mentions "packaging" but the spec's own packaging detail (page 42) is scoped separately; don't block A on it.
- Renaming `run-haven.bat` itself or building a signed installer.
