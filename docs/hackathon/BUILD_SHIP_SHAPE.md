# HAVEN × Amazon Build, Ship, Shape — significant-update overview

Spec: `HAVEN_Amazon_Build_Ship_Shape_Engineering_Spec.pdf` (The Hidden Canopy LLC, 25 Sep 2026). This file is the WP0 evidence artifact the spec's §21.2/§24.3 ask for — a short, judge-legible separation of what predates the hackathon window from what the submission actually adds. It does not restate the spec; see the spec itself for requirements, architecture, and acceptance criteria.

## Baseline

- **Baseline commit** (spec's own stated baseline, §1 Document Control): `3ed2273cd9314c295fe1f5120d7d37b02732d83a` — "closer, this is connection, event pipeline, and themes."
- No git tag has been created for it. Tagging is a repo-history action left to the team (`git tag build-ship-shape-baseline 3ed2273`), not done automatically here.
- Everything before this commit is the pre-existing HAVEN product: local-first world model, provider composition, authority engine, confirmation/deny paths, persistent rules, action receipts, knowledge/claims, native + web surfaces, and the intelligence/execution separation the spec's §2 executive summary describes. None of it was built for this competition and none of it should be presented as if it were.

## What changed in the hackathon window (`3ed2273..HEAD`)

Two independent efforts landed in this window; only the first is Build/Ship/Shape work. Keep them separated in any judge-facing narrative:

**Unrelated to the submission — a native app stability fix.** Commits `e450481`..`0e0bf7f` (Native Product Pass phases 4–7, connectivity/density/themes) and the WinUI native-crash root-cause fix bundled into `50b9a1f`/`5c7fe73` (dynamic-XAML-loading crash, a real WindowsAppSDK 1.6 heap-corruption bug, and an `App`-constructor timing bug — three independent causes of the same `0xC000027B` signature) are pre-existing product maintenance, not part of this competition's "significant update." Do not cite them in the Devpost narrative or the demo video.

**The actual significant update — External Agent Gateway (WP1):**

- `58d19c8` "External-agent support and receipt provenance" — `haven/external_agents/{domain,errors,store}.py` (connection/binding/provenance domain model, SQLite store with schema-version-refusal, typed `ExternalDenied` reason codes), plus the `HavenApplication` hooks a transport will need later: `PendingRequest.requested_by`/`external_connection_id`, `principal_for(person_id)`, and a `_confirm_direct_action(..., principal=, external_source=)` refactor.
- `5c7fe73` "aws slice 1, debug" — closes out WP1: `haven/external_agents/__init__.py` + `gateway.py` (the admission algorithm from spec §8.3 — `ExternalAgentGateway.admit()` — plus `ExternalAgentService` for owner-facing connection/binding management), 9 new IPC methods (`external_agents.connections.*`, `external_agents.bindings.*`, `.observed_subjects`, `.audit`) wired into `haven/web/server.py`, and 60 new tests across `test_external_agent_store.py`, `test_external_agent_gateway.py`, `test_external_agent_scopes.py`, `test_ipc_external_agents.py`.
- Same commits also carry the everyday Wi-Fi/Bluetooth `DiscoveryService` (`haven/web/discovery_service.py`) and its native "Discover" tab — a real-transport (SSDP + Bluetooth) discovery surface distinct from the setup wizard's one-time scan. This is HAVEN product work adjacent to, but not required by, the Amazon submission; mention it only if it strengthens the "multi-provider orchestration" story (spec G3), not as a track-technology claim.

## What is NOT done yet (tracked in `TASKS.md` under "Amazon Build, Ship, Shape")

No Alexa+ simulator, no Ring integration, no Bedrock provider, no Governed MCP OSS package. The External Agents management surface exists natively (Settings), over `/api/external-agents/*`, and in the web panel. The MCP transport decision is made and built: `native/Haven.Desktop` hosts `POST /mcp` (loopback-only, bearer-gated) via the official `ModelContextProtocol.AspNetCore` SDK, forwarding to the Python `TransportBridge` (`haven/external_agents/transport.py`, exposed as IPC `external_agents.tools.call`) — five tools (`haven_world_get`, `haven_rooms_list`, `haven_action_request/confirm/deny`), every one admitted by `ExternalAgentGateway` and governed by the existing authority engine. The native host is now verified by an official .NET MCP client test over real loopback HTTP: tool discovery, tool invocation, bearer rejection, and bridge envelope provenance all pass. A real third-party/Alexa+ client driving the packaged running app remains an external deployment/partner gate, as does WinUI pixel click-through. See `TASKS.md`'s Build/Ship/Shape section for exactly which work packages remain blocked on a team decision (Ring credentials vs. simulator-only; WP7/WP8).

## Verification as of this file

- Full existing HAVEN test suite: green (155 test files; the one documented pre-existing flake is `tests/test_web_plugins.py`'s live-network catalog test, which fails only in full-suite runs and passes in isolation — unrelated to this window's changes).
- `dotnet build -p:Platform=x64` for `native/Haven.Desktop`: 0 warnings, 0 errors.
- No secrets, credentials, or `.env` files are present in this window's diff.
