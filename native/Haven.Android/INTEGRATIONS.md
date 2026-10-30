# Android product integrations and inventory

This is a phone-facing **product integration** surface. It is not HAVEN's
`haven.plugins` Export Consumer channel, which remains restricted to explicitly
exported receipts and has no live relay. A catalog row, marketplace product or
standalone executable is not treated as a connected plugin.

## Source inventory

- **Latch:** The Hidden Canopy LLC's [Latch reference adapter](E:/HiddenCanopy/Latch/integrations/haven/adapter.py) uses the Latch Python SDK and
  `latch.ipc` version 1. It reads commitments, access grants, review proposals,
  action plans and evidence; its cancellation method calls `actions.plan` and
  intentionally has no authorize/execute/verify method. It has not been wired
  to a real HAVEN host here. `LatchAdapter` wraps that public adapter, presents
  only allow-listed fields, serializes calls and retains its stable proposal
  idempotency behavior. Evidence content is deliberately excluded from the
  phone projection. A real deployment needs a bounded, authenticated local
  transport and verified version compatibility; the existing reference SDK's
  socket has no timeout by default and does not itself establish that here.
- **Harbor:** The Hidden Canopy LLC's [Harbor HAVEN adapter](C:/Users/envyn/Documents/Codex/2026-10-29/task/harbor/clients/haven-plugin/README.md) is a WinUI owner surface;
  its currently deployed owner pipe is deny-only. Harbor's `harbor-ui` models
  have bounded metadata snapshots for vault objects, request audit, app grants
  and history. Request audit is not a pending approval queue. `HarborAdapter`
  accepts only a host-injected verified metadata reader and projects the
  corresponding metadata fields. No Android owner-verification, unlock,
  secrets, field values, or executable capability method is invented. Locked
  or unavailable states clear metadata. The Harbor worker's files are unchanged.
- **Ring simulator:** HAVEN registers `haven_ring_simulator` as a provider entry
  point in `pyproject.toml`; it provides synthetic evidence only and refuses
  live Ring mode. It is a provider, not a Latch/Harbor product screen.
- **Other real extension types:** HAVEN's extension taxonomy distinguishes
  providers, intelligence services, in-process feature modules and external
  export consumers. Existing code also includes a Home Assistant provider,
  Bluetooth/IR, computer/browser observations, and email/calendar boundaries.
  Their existence does not establish a product plugin or Android transport.
  TraceGlass appears as an export-consumer catalog reference; that catalog does
  not establish an Android live connection. No invented product screens or
  install/enable actions are offered for these entries.

## Host and phone protocol

`ProductTransport` is the Android transport seam. `ProductRules` checks the
phone-to-host `haven-product-ui-1` version, product identity and supported
operation vocabulary. This negotiates the **new phone projection**, not the
upstream Latch or Harbor protocol version. Upstream verification is an
independent host responsibility and is not complete here.

`ProductHost` only accepts the two reviewed adapter types and requires an
injected, per-product/per-operation authorizer for every call. It also uses
HAVEN's existing external-agent admission: a bound principal with `world.read`
for reads or `actions.request` for a proposal, *and* the additional product
grant. An unbound session cannot read product data. No default grant policy or
production product host is configured. Descriptor state `configured` means an
adapter was supplied, not that a live backend or its version was verified.
Failures do not fall back to local desktop APIs. Proposals use the existing
request ledger as well as Latch's own idempotency key; a failure after a
proposal starts is reported as uncertain.

Android has distinct Latch and Harbor screens. Latch supports capability-gated
commitment, access, review, plan and evidence views and a deliberate
propose-only cancellation. Harbor displays only allow-listed metadata when a
verified host actually supplies it, and gives a verification help/handoff
state instead of presenting Android device unlock as Harbor authorization.
Screens clear product metadata on backgrounding or disconnect. Light, Dark and
System are inherited from the app. No invented Latch logo or alternate mark is
shown; the selected hinged-gate hasp asset still needs to be supplied through
an approved asset path.

The fixtures exercise these interfaces and a loopback HTTPS round trip. They do
not establish live Latch/Harbor interoperability, secure owner authorization,
Android lifecycle rendering, or a production release.
