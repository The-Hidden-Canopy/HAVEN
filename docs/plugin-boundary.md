# The HAVEN plugin boundary

> **Taxonomy (spec page 41):** the user-facing surface described in this
> document is an **Export Consumer** -- an extension that runs outside
> HAVEN and reads only explicitly exported, redacted artifacts through the
> signed-catalog boundary. The product UI and docs call it that; the
> `haven.plugins` package name is unchanged for compatibility. The other
> extension classes (Provider, Intelligence Service, Feature Module) live
> under `haven/extensions/` with their own contracts.

This document defines the contract between HAVEN and a **Hub-relayed
plugin** specifically: something that wants to consume HAVEN's behavior
from entirely outside the installation, coming-soon, through the Hub. It is
easy to conflate this with two other, already-real relationships, so start
with all three:

- A **provider** runs *inside* HAVEN. It implements one of the `Protocol`
  contracts in `haven/providers` (`IntelligenceProvider`, `ExecutionAdapter`,
  `ObservationProvider`, a speech protocol, ...), is selected through the
  capability registry, and operates under the exact same authority boundary
  as HAVEN's own fixtures. See `authoring-providers.md` for that contract.
- A **local analyzer** runs locally against the household's own full
  receipt chain, never exported, never through the Hub. The diagnostic
  contract (`haven/trace/contracts.py`'s `TraceAnalyzer`) integrates this
  way -- a public Protocol in HAVEN, with any implementation kept outside
  this repository. Adaptive evaluation services are separate and have no
  HAVEN-specific integration today.
- A **Hub-relayed plugin** -- the subject of the rest of this document --
  never runs inside HAVEN, never implements a provider or local-analyzer
  Protocol, and never receives live household state. It would read only
  what a household chose to export, after the fact, relayed through the
  Hub -- never by talking to a household's HAVEN instance directly. This
  path is **not built yet**: no export mechanism, no relay, nothing moves.
  It exists for a plugin that would rather receive a redacted, narrow slice
  of evidence than be installed and run locally against the full chain.

## The one rule that matters everywhere

**A plugin only ever reads versioned, provenance-preserving receipts that a
household explicitly exported, and only ever reads them through the Hub.**
Concretely:

- HAVEN already emits a versioned JSON receipt export locally
  (`haven/audit/receipts.py`'s `ActionReceipt.to_dict()`, wired through
  `haven/web/serialize.py`) that omits confirmation material. That export
  is not yet redacted for external release, though -- it still carries
  `household_id`, `requested_by`, free-text `justification`, and action
  `parameters`, none of which a Hub-relayed plugin may ever receive. A
  Hub-relayed plugin's export path still needs its own minimal, allow-listed
  projection of this receipt, not the raw `to_dict()` output; that
  projection is part of what "not built yet" means below.
- Forwarding that export to the Hub is a local, explicit, per-installation
  decision an operator opts into. It is never a default and never silent.
  Nothing in this document should be read as implying that export path is
  built or enabled today.
- The Hub relays what was explicitly exported. It never becomes a household
  authority, never gains write access back into any household, and never
  brokers a live connection from a plugin to a running HAVEN instance. The
  Hub coordinates approved public metadata; execution and private state stay
  local.
- A plugin registers in the Hub's signed plugin catalog
  (`haven-plugin-catalog.v1`, served at `/api/haven/plugins/catalog`),
  declaring one capability from a closed set and one data boundary. The
  catalog schema can currently only express a single data boundary value --
  `exported_receipts_only` -- so a plugin asking for anything wider fails
  catalog validation outright. The rule is enforced by the schema, not
  merely written down here.
- A plugin never has a live socket, API key, or credential to any specific
  household's HAVEN instance. It cannot execute a command, propose a rule,
  or write anything back into a household's authority path. Execution and
  rule proposal stay exclusively local, provider-mediated, and
  `AuthorityEngine`-gated, exactly as `authoring-providers.md` describes.

If you find yourself wanting a plugin to poll a household directly, cache
credentials for one, or receive anything upstream of the receipt export
(the `WorldView` projection, `haven.core.store`, raw evidence) -- that is
the same failure mode `authoring-providers.md` warns providers away from,
just approached from outside the process instead of inside it. Route around
the urge, not around the boundary.

## Reference plugin, and why it's listed here at all

TraceGlass is listed in the Hub's catalog even though its primary
integration against HAVEN is the **local analyzer** path above, not this
one. It's there because the catalog is also where a future, narrower
Hub-relayed variant would be discovered -- useful for a household that
doesn't want to install an analyzer locally -- and because its closed
capability value is defined once, here:

- **`decision_chain_reconstruction`** (TraceGlass, diagnostic) --
  reconstructs a receipt as
  available-information -> inferred-belief -> candidate-action ->
  executed-action -> consequence and identifies the earliest point that
  chain broke. Strictly read-only: it produces analysis, never an action,
  and has no path back into HAVEN, whether it runs as a local analyzer
  against the full chain or, someday, as a Hub-relayed plugin against a
  redacted export.

Adaptive evaluation services are not listed. Their mechanism -- adaptively
probing a live model endpoint -- does not fit this catalog's shape: it needs
a queryable target rather than a pile of past exported receipts. The catalog
therefore does not claim that an exported-receipts boundary supports that
kind of service.

The reference catalog entry carries no privileged access semantics -- a
third-party plugin that satisfies the same catalog entry (capability,
`exported_receipts_only` boundary) is architecturally indistinguishable
from any other entry. That symmetry is deliberate.

## What the catalog entry actually is

The Hub's plugin catalog is discovery and trust metadata -- which plugins
exist, what they claim to do, and what boundary they're constrained to. It
is not a channel for household data, and listing a plugin in the catalog is
not, on its own, a live data connection to any household. See
`api/haven-plugins/` in the Hub repository for the signed catalog schema
and its validator; the schema-level constants (`authority_path:
"never_exposed"`, `data_scope: "hub_exported_receipts_only"`,
`household_pii: "never_included"`) are required on every published catalog
version, not optional fields a future entry could quietly drop.

The receipt-export path from an installation to the Hub, and the Hub-side
relay from an export to a subscribed plugin, are still integration surfaces
to be built. This document defines the contract they must satisfy when they
exist; it is not a claim that they are live today.

## Local projection guard

`haven.plugins.exports.authorized_receipt_projection` defines a small local
allowlist: schema version, outcome, authority status, whether execution was
attempted and whether it succeeded. It accepts an `ActionReceipt`, not arbitrary
dictionaries. Cross-scope receipts fail, and every call requires fresh host
authorization for the exact principal, scope and consumer. Consumer enablement
and product installation are not export authorization.

The projection excludes all household/person/receipt/device IDs, free text,
parameters, timestamps, capability names, evidence, correlation and external
source fields. Raw `ActionReceipt.to_dict()` remains local-only. No sender, Hub
relay, automatic consent or UI export action is connected to this guard.
Provenance, transport consent and relay acceptance remain blocked until their
separate contracts are implemented and reviewed.
