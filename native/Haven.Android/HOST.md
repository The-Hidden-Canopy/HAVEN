# Android host and pairing integration handoff

This code does not start a production listener or enroll a device by itself.
`ManagedBridgeHost` is an opt-in lifecycle wrapper around a bounded TLS server;
it caps worker/handshake concurrency, applies socket timeouts, and stops
accepting on shutdown. An already admitted operation can still complete after
shutdown: cancellation never rolls it back. Backend adapter calls themselves
must have bounded I/O; the host cannot safely kill arbitrary Python calls.
Certificate validation remains Android's normal platform validation; there is
no bypass, blanket trust, or certificate enrollment here.

`PairingCoordinator` is a host-side seam, not an HTTP API. A phone would create
a high-entropy verifier and send its SHA-256 digest in a short-lived pairing
request. The owner must independently compare the displayed code and approve a
specific declared household principal, scopes and bounded expiry through an
authenticated local owner surface. The phone proves possession of the verifier
at redemption. The injected issuer is absent by default and must supply a
cryptographically random, single-use-delivered bearer credential. Only its
hash is stored in HAVEN's existing external-agent connection. Pair requests and
approvals are in memory and expire on restart. The separate durable
`PairingRegistry` contains only connection IDs and an activation gate: it is
opened after the existing connection and binding have been created and closed
before revocation. `PairedSubjects` rechecks the gate, connection, binding and
expiry on every request. The existing external-agent gateway rechecks admission
and the authority engine remains responsible for each action.

The phone bridge additionally requires an injected `pending_checker` for
confirm/deny and verifies that the pending request originated on the same
external connection. Without it, confirmation fails closed. A repeat after a
pending item has been consumed may be denied rather than returning its old
receipt; inspect HAVEN for the outcome. This tightens the phone bridge only;
the underlying HAVEN `confirm_pending_as` still checks principal rather than
connection, which needs separate core review.

Before live use, an owner must deliberately enable and configure an
**authenticated** local owner/pairing UI and secure one-time credential
handoff, verify the device comparison code and selected scopes, provision a
trusted TLS identity and host-specific certificate distribution, and explicitly
start a reviewed product authorization policy. None of those adapters is wired
here; no live credential was minted. The host must also stop/rebind when HAVEN
rebuilds its director/store, audit failed or uncertain provisioning, implement
rate limiting and durable revocation recovery, and verify Latch/Harbor
transport identity, timeout and version compatibility. An interrupted delivery
may require owner review/revocation; do not silently reissue. The demo host is
synthetic and is not a production provisioning workflow.

HAVEN's current local assistant `ask`/`/api/chat` uses the local resident and
returns the full local UI state, while the external-agent contract has no chat
scope/tool. It must not be forwarded as an Android chat endpoint. A future
contract needs a bound principal, narrow scope-safe conversation projection,
explicit treatment of proposals and idempotent/uncertain outcomes, and its own
security review.

An optional provider-neutral session gateway is now defined in `haven/session_bridge`; it is not configured or attached to the live host. See `session_contract/README.md` for its separate per-session authorization requirement.
