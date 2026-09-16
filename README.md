# HAVEN

HAVEN is a local-first household intelligence layer. It is designed to sit
above Home Assistant and other device protocols, not replace them.

The governing loop is:

```text
observe -> understand household state -> predict or interpret ->
check authority -> act -> observe consequence -> remember
```

The first implementation is deliberately small and fixture-driven. It proves
the control boundary without connecting to a real home, sending a network
request, or treating a model response as permission to mutate a device.

## What exists in 0.1

The initial vertical slice contains:

- an immutable, household-scoped world snapshot for presence, context, and
  device state;
- a model-gateway boundary that can return a structured rule draft but cannot
  execute an action;
- a rule lifecycle of `PROPOSED -> APPROVED` with owner-only approval;
- an authority engine with explicit safe-automatic, confirmation-required, and
  forbidden action classes;
- a transition-only state store that emits a domain event for every accepted
  or blocked decision;
- a Home Assistant-shaped fixture adapter that records commands locally;
- an action receipt connecting the request, interpretation, evidence,
  authority decision, command, response, and event IDs, with a versioned JSON
  export that omits confirmation material;
- adversarial tests for cross-household scope, role tier, justification,
  ambiguous interpretation, stale/fallback evidence, invalid transitions,
  direct state mutation, confirmation gates, and naive timestamps.

There is no live Home Assistant client, local model runtime, camera pipeline,
mobile surface, scheduler, cloud fallback, or physical-device capability yet.

## The first household example

The phrase:

> When I'm working late, don't blast the bedroom lights when I walk in.

is accepted by the fixture model gateway as a proposal. HAVEN records the
interpretation and preserves the unresolved meaning of “don't blast”; it does
not invent a brightness percentage or silently select a scene. Approval is
blocked until the household supplies that missing parameter. The household can
then submit a new draft through the clarification transition; the original
interpretation remains in the event history and is never silently overwritten.

Once the household makes the action explicit—for example, “cap bedroom lights
at 20%”—the same path can be exercised:

1. a resident proposes the rule;
2. an owner approves it once, with a justification;
3. a fresh observed snapshot shows the resident entering the bedroom while
   `working_late` is active;
4. the authority engine allows the previously approved light action;
5. the fixture Home Assistant adapter receives a typed command;
6. HAVEN records the adapter result and returns a receipt.

This distinction is intentional. A useful household system must be able to
say “I need clarification” before it can say “I acted.”

## Package boundaries

```text
haven/
├── core/
│   ├── domain.py       # scoped facts, rules, actions, events, transitions
│   └── store.py        # immutable state and execute_transition()
├── intelligence/
│   └── gateway.py      # proposal-only model boundary and fixture interpreter
├── authority/
│   └── policy.py       # risk classification and fail-closed decisions
├── integrations/
│   └── home_assistant/
│       └── adapter.py  # protocol plus local fixture adapter
├── audit/
│   └── receipts.py     # machine-readable action receipts
└── runtime.py          # narrow orchestration of the vertical slice
```

The package does not import from a web or API layer. Integrations receive a
command only after the authority engine returns `ALLOW`. The model gateway
returns a `RuleDraft`; it has no reference to the adapter or the store.

## Governance invariants

- Household identity is checked before world evidence is joined to a request.
- Rules and action records are immutable values. State changes go through
  `HavenStore.execute_transition()`.
- Accepted and blocked transitions emit typed domain events through
  `append_domain_event(DomainEvent(...))`.
- Permission decisions use `RoleTier` and `DecisionCode` constants rather than
  ad hoc permission strings.
- `STALE`, `FALLBACK`, and `UNAVAILABLE` evidence cannot authorize an action.
- A model proposal is not an authorization and cannot reach an integration by
  itself.
- Unlocking, opening a garage, purchases, and alarm changes require a separate
  typed, time-bounded, single-use confirmation token; forbidden actions are
  denied.
- Timestamps must be timezone-aware and are normalized to UTC.
- Receipts distinguish the requested action, derived interpretation, observed
  evidence, authority decision, execution attempt, and device result.

## Run the tests

From this directory:

```powershell
python -m pytest -v
```

The test suite is the current executable contract. It uses no network, model
weights, credentials, or household data.

The confirmation object in this fixture is a local protocol value, not a
cryptographic credential issuer. A live interface must add secure issuance and
storage before any real high-consequence integration is enabled.

## Relationship to the adjacent stack

HAVEN is intentionally independent of the existing repositories at this
stage. A future integration should exchange versioned, provenance-preserving
artifacts rather than import private internals across project boundaries:

- Ghost Teacher can generate adversarial household situations and evaluate a
  model gateway or planner against them.
- HAVEN can provide real-world-shaped authority and consequence traces without
  granting Ghost Teacher execution authority.
- TraceGlass can reconstruct a HAVEN receipt as an
  available-evidence -> belief -> candidate-action -> executed-action ->
  consequence chain.

Those are integration directions, not claims that any live connection exists.

## Ownership and publication

This repository is owned by The Hidden Canopy LLC. No license file or
publication permission has been added yet. Do not treat this design or future
artifacts as permission to reuse, train on, or redistribute them.
