# Provider-neutral HAVEN session contract

This is a schema and adapter for an **existing host-supplied provider**. It does
not implement a model, autonomous agent, worker, or separate memory. No provider
is required: the Android bridge returns `session_provider_unavailable` unless a
reviewed host explicitly injects one. The fixture below is synthetic and is not
connected to desktop, web, or production storage.

## Contract and capability negotiation

`request.schema.json` and `response.schema.json` define `haven-session-1`.
The wire methods are `haven.session.capabilities`, `.sessions`, `.snapshot`,
`.replay`, `.memory`, `.command_status`, `.submit`, `.cancel`, and `.decide`.
Each request has a stable `request_id` and a versioned arguments object. No
request carries a caller-selected principal, household, role or credential
binding. The host supplies `AuthContext` after its existing authentication.

A provider declares which methods it supports. Replay is enabled only if it
also declares `durable_replay`; submit, cancel and decide require
`idempotent_commands`. These declarations are part of the provider contract,
not independent proof: a host must review and test its chosen adapter. Optional
memory reads project that provider's existing authorized memory; HAVEN creates
no new memory store. Unsupported or missing operations remain unavailable.

`SessionGateway` requires an existing bound external identity plus an explicit
per-session authorization callback on **every** call. Read methods intersect
with HAVEN's existing `world.read`; mutations intersect with `actions.request`;
those broad scopes alone do not grant session access. The callback must also
check the separate `session.read`, `session.send`, `session.cancel`, and
`session.approve` permissions and membership for the exact principal and
session. Session listings are filtered through the same grant. Unbound callers,
cross-household principals, invalid versions, malformed data and unknown
methods fail closed. No grant callback or live provider is installed by default.

The bridge admits an optional `session_host` in `BridgeServer` /
`ManagedBridgeHost`; there is no automatic listener or account enrollment.
The `ProductHost` Latch/Harbor routes remain separate and require their own
explicit per-product grants. Neither feature changes HAVEN's export-consumer
catalog boundary. TLS validation remains unchanged.

## Replay, commands and outcome semantics

Session events carry a contiguous durable sequence and a bounded projection;
the client rejects gaps, wrong sessions and incompatible versions. Memory,
messages, job status and approvals are provider projections under that same
session grant. Android and web clients do not create sessions or copy memories.

A provider must commit an idempotency key, semantic request hash and result
atomically with each command. Exact retries return the recorded result;
reusing a key for a different payload is rejected. `command_status` may return
`recorded` or `unknown`; unknown is never proof of non-execution. Network
failure, malformed mutation results, or a provider failure after dispatch is
uncertain. Neither client automatically retries a mutation with a new ID.

A `Conflict` or `SessionDenied` raised by a trusted provider is reserved for a
decision the provider can establish did **not** execute; stale approval is
therefore reported only under that contractual guarantee. Any other exception
after mutation dispatch becomes `outcome_uncertain`. Approvals require the
same session, principal, current revision, expiry and a fresh authority check.
Cancellation is a request until the provider proves the job stopped. Closing a
phone or browser view does not cancel the host's work. On host restart, a
provider must not replay side effects without proving resume safety.

## Current host findings and acceptance

The checked desktop uses `DesktopShell` to host the existing service graph.
Normal windowed `wait()` closes that shell when its window exits; explicit
background mode can keep the host running but was not enabled here. Native
`composer.ask` and local web `/api/chat` use `HavenApplication`'s local
resident and in-memory conversation. `HistoryStore` persists events, actions,
receipts and memory but not the conversation list or pending approvals.
`DownloadJobManager` is an in-memory model-download job map, not a persistent
session provider. Web SSE and the named-pipe event publisher are live updates,
not durable replay. These facts do not prove a real persistent session.

`fixture.py` and `fixture_transport.py` exist only for synthetic tests. They
use a disposable SQLite database for session/event/job/idempotency behavior and
never run an agent; an optional fixture callback supplies provider-owned memory.
The tests exercise an actual fixture-process restart, cross-interface replay,
concurrent duplicate commands, stale approvals, cancellation and unauthorized
access. A synthetic TLS round trip checks the optional bridge route. No test
establishes real desktop/provider integration, real device behavior, or a
production authorization policy.

For Sol: bind the optional adapter to the existing host's authenticated
principal, authorized session store, provider lifecycle and receipt/authority
chain. Do not instantiate the synthetic fixture as production storage. With
synthetic data, open one session in desktop, web and Android; close a view
while a host job continues; reconnect and replay without duplication; interrupt
and reconcile a stable command ID; reject foreign scopes and stale approvals;
then restart the real host and verify its own durable history and safe recovery.
A host-side background lifetime, trusted certificate setup and owner-approved
session grants remain separate from the Android client's foreground lifecycle.

## Client integration seams

`SessionClient.cs` is a desktop .NET client seam; `shared_session_client.mjs`
is the web equivalent and `SessionClient.java` is used by Android. Each takes
an existing transport, negotiates capability and guarantees, validates the
response session, and leaves authentication and provider lifecycle to the host.
None is wired into Sol's desktop or web components here. The desktop seam's
no-package synthetic test can run with `dotnet run --project
native/Haven.Android/session_contract/dotnet-tests/SessionClient.Tests.csproj`.
