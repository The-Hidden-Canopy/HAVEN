# The HAVEN plugin boundary

This document defines the contract between HAVEN and anything outside it
that wants to consume HAVEN's behavior -- Ghost Teacher, TraceGlass, or any
future third party. It is a different relationship from the one
[`docs/authoring-providers.md`](authoring-providers.md) defines, and the two
are easy to conflate, so start with the distinction:

- A **provider** runs *inside* HAVEN. It implements one of the `Protocol`
  contracts in `haven/providers` (`IntelligenceProvider`, `ExecutionAdapter`,
  `ObservationProvider`, a speech protocol, ...), is selected through the
  capability registry, and operates under the exact same authority boundary
  as HAVEN's own fixtures. See `authoring-providers.md` for that contract.
- A **plugin** never runs inside HAVEN, never implements a provider
  `Protocol`, and never receives live household state. It is a separate
  product that reads what a household chose to export, after the fact,
  through the Hub -- never by talking to a household's HAVEN instance
  directly.

## The one rule that matters everywhere

**A plugin only ever reads versioned, provenance-preserving receipts that a
household explicitly exported, and only ever reads them through the Hub.**
Concretely:

- HAVEN already emits a versioned JSON receipt export locally
  (`haven/core/receipts.py`, `haven/core/serialize.py`) that omits
  confirmation material. That export is the only artifact a plugin ever
  sees a version of.
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

## Reference plugins

Two plugins exist today as products in their own right and are the
reference implementations of this contract:

- **TraceGlass** -- capability `decision_chain_reconstruction`.
  Reconstructs a HAVEN receipt as an
  available-information -> inferred-belief -> candidate-action ->
  executed-action -> consequence chain and identifies the earliest point
  that chain broke. Strictly read-only: it produces analysis, never an
  action, and has no path back into HAVEN.
- **Ghost Teacher** -- capability `adaptive_curriculum_evaluation`. Uses
  exported, redacted evaluation signals -- where a local intelligence
  provider under-performed against a household-shaped situation -- to
  generate adversarial test cases and propose training curriculum back to
  the IDA training pipeline. It targets training data for the next model,
  never a live household, and gains no execution authority anywhere.

Both are Hidden Canopy products, not community plugins, but they hold no
special access under this contract -- a third-party plugin that satisfies
the same catalog entry (capability, `exported_receipts_only` boundary) is
architecturally indistinguishable from either one. That symmetry is
deliberate, the same way `haven/providers` treats a free fixture and a paid
Hidden Canopy provider identically.

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
