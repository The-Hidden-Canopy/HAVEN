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
- a Home Assistant-shaped fixture adapter that records commands locally,
  alongside a live REST adapter (`LiveHomeAssistantAdapter`) that implements
  the same `execute(command) -> DeviceResult` contract against a real Home
  Assistant instance -- the only network I/O in Haven, isolated to
  `haven/integrations/home_assistant/client.py` and never touched by tests;
- an observe side for the same integration: `fetch_states()` reads Home
  Assistant's state list and `device_states_from_ha()` maps it onto
  `DeviceState` evidence for registry-known devices only (HA's
  "unavailable" becomes `EvidenceStatus.UNAVAILABLE`, already fail-closed);
- a single-shot `HomeAssistantObserver` that assembles a full
  `WorldSnapshot` -- devices plus declared presence/context sources -- from
  one fetch. Presence and context meaning is declared per deployment
  (`PresenceSource`/`ContextSource`), never inferred from entity names, and
  no polling loop lives in Haven: a deployment calls `observe()` on its own
  cadence;
- an action receipt connecting the request, interpretation, evidence,
  authority decision, command, response, and event IDs, with a versioned JSON
  export that omits confirmation material;
- adversarial tests for cross-household scope, role tier, justification,
  ambiguous interpretation, stale/fallback evidence, invalid transitions,
  direct state mutation, confirmation gates, and naive timestamps.

There is no local model runtime, camera pipeline, mobile surface, scheduler,
cloud fallback, or physical-device capability yet.

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
├── providers/
│   ├── capabilities.py # capability registry: kind + capability, no licensing
│   └── defaults.py     # registers the fixture gateway as a default provider
├── devices/
│   ├── manifest.py     # device capability manifests and control classes
│   └── registry.py     # device discovery by type or required capability
├── authority/
│   └── policy.py       # risk classification (ActionKind or capability) + fail-closed decisions
├── alerts/
│   ├── models.py       # Alert, AlertRule, AlertSeverity, conditions
│   └── engine.py       # deterministic, read-only correlation -> Alert
├── cameras/
│   ├── manifest.py     # camera hardware capabilities (live_stream, ptz, ...)
│   └── registry.py     # camera discovery by room or required capability
├── integrations/
│   └── home_assistant/
│       ├── adapter.py  # protocol plus local fixture adapter
│       ├── client.py   # live REST adapter -- the only network I/O in Haven
│       ├── observer.py # one-shot fetch -> WorldSnapshot assembly
│       └── state.py    # maps HA state dicts onto DeviceState evidence
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
- The provider registry answers "is a capability available", never "which
  vendor backs it"; licensing and authentication stay inside a provider
  plugin and never enter `haven/providers`.
- A rule may name a device `capability` instead of relying on `ActionKind`
  alone. When it does, `AuthorityEngine` resolves risk from that device's
  manifest via `risk_for_request()`, mapping `ControlClass` onto the same
  `RiskTier` vocabulary as the closed `ActionKind` set (`MEDIUM` uses
  `RiskTier.CONDITIONAL`, previously unused). A request naming a capability
  that no registered manifest can resolve is `DENY`/`UNKNOWN_CAPABILITY`; it
  never silently falls back to `ActionKind`-based classification.
- `ActionKind` still exists and still drives the device-command `service`
  mapping for a rule with no `capability`, via `runtime.py`'s
  `_service_for()`. A rule that does name a `capability` is routed instead
  through that device's manifest: `HavenRuntime._service_for_draft()` reads
  the `service` a writable `CapabilityDescriptor` declares, using the same
  `device_registry` that `AuthorityEngine` already used to authorize it, so
  the two never diverge. A writable capability must declare a `service` at
  manifest-construction time -- there is no runtime fallback for one that
  doesn't.
- A device's `role` is its `semantic_role` if declared, else its
  `device_type`. This is what lets a smart plug wired to a lamp
  (`device_type="switch"`, `semantic_role="light"`) resolve against "turn
  off the bedroom lights" the same way a real light entity does.
  `DeviceRegistry.find()` filters on `role` and `room` for this.
- A `RuleDraft` sets exactly one of `target_device_id` or `target_selector`
  (a `DeviceSelector`), never both and never neither. A selector-based rule
  cannot run through `HavenRuntime.run_rule()`; it runs through
  `run_rule_for_group()`, which resolves the selector against
  `AuthorityEngine.device_registry` and re-runs the ordinary single-device
  authority and execution path once per resolved device, independently. A
  GUARDED device blocking on confirmation does not hold up a LOW_RISK device
  resolved into the same group.
- A household member changing a device directly outranks automation: when a
  `DeviceState.changed_by` is `ChangeOrigin.HUMAN` and `now` falls within
  `AuthorityEngine.human_override_window` (default 90 minutes) of that
  observation, `decide()` denies with `HUMAN_OVERRIDE_ACTIVE` regardless of
  the action's risk tier -- checked before `CONFIRMATION_REQUIRED`, so a
  fresh manual change suspends a rule outright rather than merely asking for
  confirmation to override it. `changed_by` defaults to `ChangeOrigin.SYSTEM`,
  so nothing changes for a `DeviceState` that doesn't set it.
- A `RuleDraft.expires_at` makes a rule ephemeral without a second execution
  path: "turn this fan off in 40 minutes" is the same `propose_draft` ->
  `approve_rule` -> `run_rule` flow as a permanent rule, with `now` past
  `expires_at` denied as `RULE_EXPIRED`. This is checked as a rule-lifecycle
  gate, before device/risk resolution, alongside `RULE_NOT_APPROVED` and
  `NEEDS_CLARIFICATION`. A draft with no `expires_at` (the default) never
  expires.
- `PresenceState`/`ContextState`/`DeviceState` each carry a `confidence:
  float = 1.0`. `AuthorityEngine.minimum_confidence` (default `1.0`, fail
  closed) is checked in `evaluate_trigger()` alongside freshness; evidence
  below it blocks with `LOW_CONFIDENCE_EVIDENCE`/`UNAVAILABLE`, the same
  shape as stale or unavailable evidence. This is the seam a probabilistic
  provider -- vision, IR -- plugs into: nothing in this repo produces a
  sub-1.0 confidence value yet, and Haven Core requires none to work. A
  household opts into trusting less-than-certain evidence by explicitly
  lowering `minimum_confidence`; it is never a silent default.
- A `RuleDraft` sets exactly one of a presence trigger (`trigger_person_id`
  + `trigger_room_id`) or a `PredictionTrigger` (`event`, `min_confidence`,
  optional `subject_id`) -- predictions become inputs to routines through an
  explicit, owner-approved rule, never as an unrestricted AI decision. A
  `PredictionTrigger` is judged against the confidence bar *that rule*
  declared, not `AuthorityEngine.minimum_confidence` (which governs observed
  evidence and is unrelated); the most recent matching, not-yet-future
  `Prediction` in `WorldSnapshot.predictions` is used, and a miss or a
  below-bar match blocks with `EVIDENCE_MISSING`/`LOW_CONFIDENCE_EVIDENCE` --
  the same codes observed evidence already uses. A matched prediction is
  recorded on the receipt as `EvidenceStatus.DECLARED`, not observed fact.
  Nothing in this repo produces a `Prediction` yet; this is the contract a
  prediction engine -- a world model, Ghost Teacher -- plugs into.
- `haven/alerts` is `AuthorityEngine`'s read-only sibling: `AlertEngine`
  never touches a device, rule, or the store, and only ever returns an
  `Alert` or `None`. An `AlertRule` correlates conditions -- presence,
  context, and/or a `PredictionTrigger` -- with AND semantics, so "person
  detected" only becomes an alert alongside the other conditions the
  household declared (e.g. "AND household away"); a rule with zero
  conditions is rejected at construction rather than allowed to fire on
  every evaluation. Each condition is subject to the same `evidence_problem`
  freshness/confidence check `AuthorityEngine` uses, via a `minimum_confidence`
  the rule itself declares. Camera/vision/thermal management (discovery, PTZ,
  recording, retention, stream health) is not part of this repo: `AlertEngine`
  is the policy layer those observations feed once a real provider exists.
- `haven/cameras` is discovery only, mirroring `haven/devices`: a
  `CameraManifest` declares hardware capabilities (`live_stream`, `ptz`,
  `optical_zoom`, `microphone`, `speaker`, `infrared_mode`,
  `privacy_shutter`, `recording`) as flat booleans, not routed actions --
  there is no control class or service here, because nothing in this repo
  can move a PTZ motor or start a recording yet. A provider that finds a
  camera on the network registers it with `CameraRegistry`; nothing here
  performs ONVIF/RTSP/NVR discovery itself.
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

## License and ownership

HAVEN is open source under the Apache License, Version 2.0 -- see
`LICENSE`. Copyright 2026 The Hidden Canopy LLC.

Running Haven means running software that can act on a real household, and
that responsibility belongs to whoever deploys it: securing credentials and
confirmation issuance, declaring presence/context sources, setting override
windows and confidence thresholds, and deciding which integrations to trust.
The license's no-warranty terms are not boilerplate here -- they are the
deployment model.

The license covers this repository only. Hidden Canopy's premium model and
provider implementations are separate products; the provider contracts in
`haven/providers` exist so those can plug in without Haven depending on
them, and so anyone else's providers can too.
