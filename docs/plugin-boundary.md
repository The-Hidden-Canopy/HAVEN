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
  receipt chain, never exported, never through the Hub. TraceGlass
  (diagnostic: `haven/trace/contracts.py`'s `TraceAnalyzer`) integrates
  this way -- a public Protocol in HAVEN, a private implementation
  elsewhere, same split as a provider, applied to deeper receipt-chain
  analysis instead of a runtime capability. This is TraceGlass's primary
  path against HAVEN specifically. (Ghost Teacher is a separate,
  portfolio-wide adaptive evaluation system -- see its own engineering
  spec -- with no HAVEN-specific integration today; an earlier draft of
  this document claimed it had a local contract here the same way
  TraceGlass does, and that was wrong.)
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
  brokers a live connection from a plugin to a running HAVEN instance. (This
  mirrors the same rule that already governs the public Hub's relationship
  to Neural Forge and to Ask IDA CLI: the Hub coordinates approved public
  metadata; execution and private state stay local.)
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

Ghost Teacher is not listed. Its actual mechanism -- adaptively probing a
live model endpoint, per its own engineering spec -- doesn't fit this
catalog's shape at all: it has no use for a pile of past exported receipts,
it needs a queryable target. A prior version of this catalog listed it
under `adaptive_curriculum_evaluation` with an `exported_receipts_only`
boundary; that was a guess made without the real spec and has been removed
rather than left as a plausible-sounding but inaccurate entry.

TraceGlass is a Hidden Canopy product, not a community plugin, but under
this Hub-relay contract specifically it would hold no special access -- a
third-party plugin that satisfies the same catalog entry (capability,
`exported_receipts_only` boundary) would be architecturally
indistinguishable from it. That symmetry is deliberate, the same way
`haven/providers` treats a free fixture and a paid Hidden Canopy provider
identically.

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
