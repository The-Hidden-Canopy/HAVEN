# The HAVEN plugin boundary

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
  (diagnostic: `haven/trace/contracts.py`'s `TraceAnalyzer`) and Ghost
  Teacher (training and improvement: `haven/curriculum/contracts.py`'s
  `CurriculumEvaluator`) both integrate this way today -- a public
  Protocol in HAVEN, a private implementation elsewhere, same split as a
  provider, applied to deeper receipt-chain analysis instead of a runtime
  capability. This is their primary path.
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

## Reference plugins, and why they're listed here at all

TraceGlass and Ghost Teacher are listed in the Hub's catalog even though
their primary integration is the **local analyzer** path above, not this
one. Two reasons: the catalog is also where a future, narrower Hub-relayed
variant of either would be discovered, and the closed capability vocabulary
(`decision_chain_reconstruction`, `adaptive_curriculum_evaluation`) is
shared between both paths, so it's defined once, here:

- **`decision_chain_reconstruction`** (TraceGlass, diagnostic) --
  reconstructs a receipt as
  available-information -> inferred-belief -> candidate-action ->
  executed-action -> consequence and identifies the earliest point that
  chain broke. Strictly read-only: it produces analysis, never an action,
  and has no path back into HAVEN, whether it runs as a local analyzer
  against the full chain or, someday, as a Hub-relayed plugin against a
  redacted export.
- **`adaptive_curriculum_evaluation`** (Ghost Teacher, training and
  improvement) -- the counterpart question asked of the same kind of
  receipt chain: not what happened, but what the next training run should
  target. Produces training signals and curriculum proposals, never an
  action, never execution authority, whether local or Hub-relayed.

Both are Hidden Canopy products, not community plugins, but under this
Hub-relay contract specifically they would hold no special access -- a
third-party plugin that satisfies the same catalog entry (capability,
`exported_receipts_only` boundary) would be architecturally
indistinguishable from either one. That symmetry is deliberate, the same
way `haven/providers` treats a free fixture and a paid Hidden Canopy
provider identically.

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
