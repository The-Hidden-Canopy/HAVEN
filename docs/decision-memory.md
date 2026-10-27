# Decision memory and documentation maintenance

HAVEN retains a scoped register of questions, options, observations, choices
and concise rationale. It complements projects, tasks, claims and receipts.
Conversation lines are still transient context; they do not become accepted
project decisions automatically.

## Current state and retained revisions

`haven.decisions.DecisionStore` stores one complete JSON snapshot per scope in
`decisions.db`, using the same SQLite persistence pattern as resources and
history. A snapshot has a schema version, a monotonically increasing
generation and stable `decision:` identifiers. Each record has an ordered
revision history; the final revision is its one current state.

Each revision includes the problem or question, identified options,
observations or hypotheses, the selected option, a concise rationale,
source resource IDs and exact content revisions, constraints, an owner,
decision dependencies, affected documents, and reconsideration triggers.
It also records the acting principal, time and historical review reference.
Rationale is a short explanation for the next person, never a hidden model
reasoning trace. Human-readable `AGENTS.md` instructions remain separate
from these structured records.

The lifecycle is explicit:

```text
proposed -> accepted -> challenged -> proposed -> accepted
                    \-> superseded -> replacement decision
```

Editing a decision creates a new proposal and retains its prior acceptance
and challenges. Supersession requires an accepted replacement in the same
scope. Superseded records cannot be revived. Missing, cross-scope, self and
cyclic dependency or replacement references are rejected, including invalid
historical replacement cycles. JSON decoding rejects unknown schema
versions and corrupt snapshots without discarding the stored data.

Schema v1 migration retains a legacy current record as a proposal that needs
fresh review. The next authorized mutation persists schema v2. Migration
does not infer consent from old status or approval metadata.

## Reconcile before reuse

`DecisionService.context()` returns only accepted decisions whose evidence
still matches the current `ResourceStore` content hashes. Missing, stale,
cross-scope, future-dated or expired observations exclude the decision.
An unresolved dependency also excludes dependent decisions. Reconsideration
triggers are review instructions; HAVEN does not pretend to evaluate an
arbitrary sentence automatically. Explicit challenges and actual source
changes bring the record back to review.

Affected documents are work to complete, not comments to accumulate. Each
impact names the existing resource, its baseline content revision and the
required change. The accepted choice remains outside active context until
all affected documents are reconciled. Revise the existing documentation
through its owning editor or authorized provider, then re-observe it.
`reconcile_document()` requires a different, actually observed content hash,
a receipt reference and an owner rationale. It retains the old revision and
binds the new current revision to the updated document. A receipt string
without a matching observation cannot mark the work complete. Subsequent
document drift excludes the decision again.

The register does not write arbitrary files or introduce a second file
permission system. Its documentation reconciliation receipt confirms the
provider's observed revision; semantic correctness of the edited prose is
part of the human review. The current operating-loop documentation is
maintained in place alongside this contract.

## Existing authority remains authoritative

The service uses HAVEN's current `IdentityProvider` memberships and scope
status for every read and write. Mutations require a declared household
owner and current member/owner standing; accepting, challenging,
superseding, reconciling or deleting also requires the existing owner role
and an owner membership in that specific scope.
Identity, membership and owner state are checked again immediately before
commit. The raw store is an internal persistence component, not a public
authorization endpoint.

Snapshots, review references and checkpoint generations contain no grants,
tokens or executable operations. Resuming a checkpoint does not revive a
revoked membership. An external action derived from a decision must still
cross its provider's current authority, consent and confirmation boundary.
External effects and their idempotency remain owned by that action service.

`DecisionSource` projects unresolved decisions into the existing Needs You
queue with a review route. It never supplies an approval operation. A host
may inject `DecisionService.context()` into a bundled feature as a read
callback. The feature receives JSON evidence, not the store or identity.
HAVEN works locally without Crew; any future Crew adapter must leave Crew
authoritative over its own records and import only explicit proposals.

## Concurrency, recovery and deletion

Writes use `BEGIN IMMEDIATE`, full SQLite synchronization, a checked
snapshot generation and an expected decision revision. Concurrent edits
conflict; callers must reload and reconcile rather than silently overwrite.
A failed check or interrupted transaction restores the last complete JSON
snapshot. There is no stale backup fallback that could resurrect erased
records. Handles close after each call, including on Windows.

The active projection is limited to 12 decisions and a 12 KB entry budget.
Each request evaluates at most 64 decision records and 256 distinct source
observations, sharing cached dependency results within that request. Decisions
that exceed the evaluation budget require a focused review instead of being
treated as current. An explicit decision ID narrows the candidate search.
`DecisionContext` caps the full serializable provider projection at 16 KB
and contains detached JSON only. A scope is bounded to 1,000 decisions,
200 revisions per decision, 64 reference levels and an 8 MB snapshot; exceeding a limit requires
explicit retention work rather than unbounded accumulation.

The owner can erase a decision or all decision content in a scope. A record
still referenced by retained history cannot be erased alone; erase or
reconcile the referencing records first. Scope erasure keeps an empty
generation marker so stale checkpoints conflict. SQLite secure deletion
removes old cell content from the live database. Exports, provider files,
operating-system snapshots and external backups have their own retention
and must be deleted through their owners. The service creates none of those
copies automatically.

## Host integration

Construct `DecisionService` with `DecisionStore(data_dir / "decisions.db")`,
the existing identity and resource services, the existing declared-owner
callback and the host clock. On a data-directory rebuild, reconstruct these
bindings together. Convert source and document JSON to `SourceRevision` and
`DocumentImpact` at the boundary; accept only supported service fields.

`DecisionAPI(decisions=service, default_scope=scope_callback).handlers()`
returns shared `decisions.snapshot`, `.context`, `.propose`, `.revise`,
`.review`, `.reconcile_document` and `.erase` handlers for the existing IPC
and HTTP adapters. The adapter rejects unknown fields and caller identity or
permission injection. Mutation callers provide both
`expected_generation` and `expected_revision` where applicable. Build
`AgentContext.decision_memory` from
`DecisionContext.from_dict(service.context(scope_id))` immediately before
calling the intelligence provider. Recompute it on each request; do not
cache it in a checkpoint or use client-provided context as authority.

Focused verification includes a separate-process restart, process exit
inside an uncommitted transaction, competing writers, schema migration,
invalid history links, stale evidence, actual document re-observation,
membership revocation after checkpoint, deletion and bounded provider data.
Shared composition and repository-wide verification are separate acceptance
gates owned by the host integration workflow.

The native Memory page exposes the decision register, proposal form and
explicit direction review. Settings exposes the bundled decision inspector.
Both use the same service handlers as HTTP: `GET /api/decisions`,
`GET /api/decisions/context`, and `POST /api/decisions/<operation>`.
The authenticated host chooses the current principal; the default scope is
the current identity's personal scope. Requests may select another currently
visible scope without gaining membership or owner standing.

Domain changes commit before the bounded receipt is projected through the
existing history service. If that projection fails, the adapter reports
`outcome_uncertain`; inspect the current snapshot and its generation before
retrying. A stale generation conflicts rather than repeating a mutation.
Decision state is included in installation moves and backups. Restore retains
the existing restart requirement; restoring an old snapshot never grants
permissions absent from current identity and membership services.
