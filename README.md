# HAVEN

HAVEN is a local-first assistant for your computer, your information, and the
connected world around you. Home Assistant and other device protocols are
providers inside that world, not a separate HAVEN product or mode.

The governing loop is:

```text
observe -> understand household state -> predict or interpret ->
check authority -> act -> observe consequence -> remember
```

The governing core started deliberately small and fixture-driven, to prove
the control boundary before connecting to anything real. That boundary is
still exactly as strict, but it is no longer fixture-only: HAVEN today
connects to a real Home Assistant installation over real network I/O, runs
real native hardware (Bluetooth, microphone/speaker audio), loads and runs
real local models, and persists a real household's history across restarts.
A model response is still never permission to mutate a device -- that
invariant hasn't moved an inch -- but "prove the boundary" has become
"run a real household through the boundary."

## What exists today

HAVEN runs two compositions behind the same web surface and the same
`HavenApplication` controller: a demo household (`DemoDirector`, a scripted
fixture scenario -- no network, no credentials, no real hardware, useful for
trying HAVEN or developing against it) and a real household
(`build_application`, wired to whatever provider and hardware the
installation actually has). Nothing about the authority/evidence/receipt
core differs between them; only the world and execution wiring underneath
does. What exists spans both, including:

- an immutable, household-scoped world snapshot for presence, context, and
  device state;
- an intelligence-provider boundary (agent-agnostic: an LLM, a deterministic
  planner, an ensemble, or the built-in fixture) that can return a
  structured rule draft but cannot
  execute an action;
- a rule lifecycle of `PROPOSED -> APPROVED` with owner-only approval;
- an authority engine with explicit safe-automatic, confirmation-required, and
  forbidden action classes;
- a transition-only state store that emits a domain event for every accepted
  or blocked decision;
- a Home Assistant-shaped fixture adapter that records commands locally,
  alongside a live REST adapter (`LiveHomeAssistantAdapter`) that implements
  the same `execute(command) -> DeviceResult` contract against a real Home
  Assistant instance -- real network I/O, isolated to
  `haven/integrations/home_assistant/client.py`, and a second real-network
  module, `haven/integrations/wifi/ssdp.py` (SSDP/UPnP discovery); neither
  is touched by tests, which mock the socket/HTTP boundary instead;
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
  direct state mutation, confirmation gates, and naive timestamps;
- a zero-dependency local web surface (`haven/web`, stdlib HTTP + SSE,
  zero-build vanilla UI) that renders the simulated household and drives its
  glow state language directly from real `AuthorityDecision` outcomes —
  confirmation-required decisions pulse, evidence problems read as critical,
  and nothing glows on its own;
- a zero-dependency desktop host (`haven/desktop`) that keeps that same
  HTML/CSS/JS renderer, starts the loopback server, opens it in a native
  Edge app window, binds the launch to a fresh HTTP-only session cookie, and
  exposes a renderer-facing host capability seam, currently a Windows folder
  picker. The browser surface reports no native host capabilities and remains
  available for development and tablet-sized hosts;
- a voice input surface on that web layer: an honest
  `dormant -> wake -> listening -> interpreting` session with a refractory
  window and interruption, where spoken commands take exactly the same
  authority path as typed ones and "stop" never pretends to recall an
  executing action;
- the public HAVEN Speech contract (`haven/speech`, stdlib only): the speech
  event vocabulary, provider protocols for wake word / VAD / streaming ASR /
  interruptible TTS, a pre-roll ring buffer, an energy-VAD reference
  implementation, and a session layer that structurally cannot execute from
  a partial transcript and discards raw audio on close unless explicitly
  retained. Fixture providers register in the capability registry under
  `wake_word` / `vad` / `speech_to_text` / `text_to_speech`, so a real
  provider binds by capability, never by vendor;
- the HAVEN Model Manager (`haven/models`, stdlib only): every model family
  — intelligence, speech, vision, embeddings, prediction, specialized —
  enters through the same three paths (Download from a curated catalog or
  manifest URL, Load Local from a folder or model root, Register External
  endpoint), normalizes into one `ModelDescriptor`, and moves through an
  explicit lifecycle (`DISCOVERED → INSPECTED → REGISTERED → VERIFIED →
  READY → LOADED`) with named failure states (`INCOMPLETE`, `UNSUPPORTED`,
  `HASH_MISMATCH`, `LICENSE_UNKNOWN`, `BACKEND_MISSING`, `LOAD_FAILED`,
  `UNREACHABLE`) that never collapse to "model unavailable". The universal
  manifest `haven-model.json` (schema `haven-model-1`) lets anyone publish
  a compatible model; `resolve(kind, requires, languages)` routes by
  capability and language only — never by model name, architecture, or
  backend. Inference backends are plugins: the reference set ships an
  `http` backend that is fully real on the standard library, plus lazy
  `transformers` / `llama_cpp` / `onnx` backends that activate only when
  their runtime is importable and surface as `BACKEND_MISSING` otherwise;
  community backends (mlx, rocm, coreml) register the same way. Multiple
  models load simultaneously (wake + VAD + ASR + agent + vision is the
  expected topology, not single-select). The same services drive a CLI
  (`python -m haven.models`) and the Settings → Models web view, and
  inspect-before-install is structural: fetching a manifest never downloads
  weights. Downloads run as background jobs with byte-total progress,
  cooperative cancel, and `.part` resume, streamed to the UI over SSE.
  Ordinary model folders (a lone GGUF, a Hugging Face checkout, an ONNX
  export) are recognized and synthesized into manifests, and repository
  URLs resolve without HAVEN hardcoding any vendor. The runtime bridge
  closes the loop: a loaded `intelligence` model becomes the
  `IntelligenceProvider` the runtime calls — with a bounded, serializable
  `WorldView` projection (freshness and confidence per item, never the
  store) so agents can answer household questions — while the scripted
  provider remains the floor when no model is loaded: models upgrade
  HAVEN, they are never required by it;
- a first-class intent union (`haven/intelligence/intents.py`):
  `QueryRequest | ActionProposal | RuleDraft | ClarificationRequest |
  ConversationMessage`, proposed through the agent seam itself —
  `IntelligenceProvider.interpret_intent(...)` returns exactly one form, so
  any provider (LLM, planner, deterministic parser, a community agent)
  classifies generically while HAVEN routes uniformly; a reusable
  household-grounded `DeterministicIntentInterpreter` is the zero-model
  floor. Queries answer from bounded evidence, rules keep the governed
  lifecycle, and one-shot commands take a separate authority entry —
  `AuthorityEngine.decide_direct()` / `HavenRuntime.run_action()` —
  where a household member's own command is the authorization: same scope,
  role, justification, risk, and confirmation-token checks as automation,
  but no rule lifecycle, no trigger, and no human-override suspension, and
  ZERO rules created in the store. Provenance is typed, never string-matched:
  `ActionOrigin.RULE | DIRECT` on every `ActionRequest`, enforced by both
  engine entries and by the store's transition gate. Every backend
  normalizes to `ChatResult`/`InferenceResult` before HAVEN sees output,
  and the enriched `WorldView` projection (readable names, capabilities,
  attributes, recent transitions, explicit uncertainty) is what agents
  receive — never the store;
- a scheduler (`haven/scheduler/engine.py`) that is another requester, never
  a privileged bypass: a due schedule asks `HavenRuntime.run_rule()` to run
  an already-approved rule through the exact same authority path as any
  other requester -- household scope, role tier, evidence freshness and
  confidence, human override, risk tier, and confirmation all still apply,
  and a policy-blocked schedule records the ordinary blocked receipt rather
  than silently not firing. The scheduler's own principal is an
  unremarkable household member; what grants authority is the owner's prior
  approval of the rule, not the scheduler's role;
- a production composition root (`haven/web/application.py`) separate from
  the demo one: `build_application()` reads a household's persisted setup
  and builds the real installation on every normal boot, including a fresh
  install with no provider configured yet. That fresh installation gets a
  real, persisted `household_id` (a minted UUID, never a shared literal), an
  empty world, enrolled devices when present, declared people, and persisted
  rules. Only an explicit demo run builds the fixture. A household that has
  connected any provider never falls back to the demo fixture again, even if that
  provider is one this composition root has no live adapter for yet (a
  community provider) or its connection details are temporarily unreadable
  -- it gets its own real installation with a world that honestly reports
  no live evidence, never a fictional house that looks real. The production
  controller (`HavenApplication`, `haven/web/haven_application.py`) and the
  demo one (`DemoDirector`) are the same class hierarchy: `HavenApplication`
  carries every real behavior once (chat, permissions, voice, scheduling,
  state projection, persistence) and never constructs a `SimulatedHouse` or
  a fixture identity; `DemoDirector` is the thin subclass that adds exactly
  those for the local scenario;
- durable persistence across restarts: rules survive in `rules.json`
  (`haven/web/rules_persist.py`), and a household's events, actions,
  receipts, and memory survive in a per-installation SQLite file
  (`haven/web/history_persist.py`, `HistoryStore`) -- one table per kind, a
  fresh connection per operation, confirmation tokens never persisted;
- a real native hardware loop for voice, not only the browser stand-in: a
  ctypes binding straight to Windows' WinMM API
  (`haven/speech/native_audio.py`, no third-party audio package, matching
  the same zero-dependency native-ABI approach as Bluetooth below) provides
  a real microphone `AudioSource` and speaker `PlaybackSink`, double-buffered
  so a synthesizer's chunk boundaries never starve the device into an
  audible gap, with a barge-in path that truncates playback within one
  chunk. `haven/web/voice_runtime.py` composes a real, always-on
  `SpeechService` from whatever wake/ASR/TTS models a household has
  assigned, and `HavenApplication.start_voice()`/`stop_voice()` wire it into
  the running application; a household with real spoken *output* but input
  handled by another always-on pipeline entirely is a first-class
  configuration, not a special case. A reference native TTS backend
  (`haven/models/backends/piper_native.py`) shells out to the MIT-licensed
  Piper executable and resamples its own voice's sample rate to HAVEN's
  pinned 16 kHz wire contract with a small stdlib-only linear resampler, so
  a household is not confined to the one Piper voice already at that rate;
- `python scripts/verify.py`, the local substitute for paid CI: it runs the
  full test suite and reports Haven's commit, the Python version and OS
  that ran it, and total/passed/failed/skipped counts, splitting skips into
  "this host lacks a real device/toolchain/optional package" (expected on
  most machines) versus anything else (worth a second look). The
  development rule it exists to enforce: a commit is not a release
  candidate unless this passes.

Camera streaming/recording and a mobile app surface are still not part of
this repo (camera *actuation* -- pan/tilt/zoom/privacy-shutter through the
same authority path as any other device -- already is, see below). HAVEN
does not have, and does not plan, a cloud fallback: local-first is a design
constraint here, not a temporary gap waiting to be filled in.

## The first household example

The phrase:

> When I'm working late, don't blast the bedroom lights when I walk in.

is accepted by the fixture intelligence provider as a proposal. HAVEN records the
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
│   └── gateway.py      # proposal-only intelligence provider boundary + fixture
├── speech/
│   ├── events.py       # public speech event vocabulary (WakeEvent, TranscriptFinal, ...)
│   ├── protocols.py    # WakeDetector / Vad / SpeechRecognizer / SpeechSynthesizer
│   ├── session.py      # invariant-enforcing session: never act on a partial
│   ├── ring_buffer.py  # pre-roll capture so wake never eats the utterance start
│   ├── vad/energy.py   # reference energy VAD, pure Python
│   ├── wake_model.py   # KwsModelManifest + ThresholdedWakeDetector (scorer = the artifact seam)
│   ├── shims.py        # loaded model handles -> SpeechRecognizer/Synthesizer/WakeDetector
│   ├── sources.py      # AudioSource protocol; WavFileSource (real audio, no mic needed)
│   ├── sinks.py        # PlaybackSink protocol; WavFileSink; NullSink
│   ├── native_audio.py # real WinMM mic/speaker: ctypes ABI, double-buffered, no third-party audio package
│   ├── service.py      # SpeechService: the always-on runtime (capture -> KWS -> VAD -> ASR -> session)
│   ├── fixtures.py     # scripted providers registered in the capability registry
│   └── providers/      # thin inference adapters (HTTP seam to a household inference stack)
├── scheduler/
│   └── engine.py       # due schedules ask HavenRuntime.run_rule(); never a privileged bypass
├── models/
│   ├── contracts.py    # ModelKind / ModelSource / ModelDescriptor (normalized form)
│   ├── manifest.py     # universal haven-model.json (schema haven-model-1)
│   ├── registry.py     # persistent model records + lifecycle states
│   ├── discovery.py    # scan model roots; candidates are never auto-activated
│   ├── detect.py       # recognize ordinary HF/GGUF/ONNX folders; synthesize manifests
│   ├── downloader.py   # inspect-before-install URL flow + HF repo resolution
│   ├── jobs.py         # background downloads: progress, cancel, .part resume
│   ├── backends/       # ModelBackend protocol + reference set (http real; ML lazy; piper_native.py real native TTS)
│   ├── bridge.py       # loaded models become providers; WorldView feeds agents
│   └── manager.py      # ModelManager: 3 entry paths, resolve, load/unload topology
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
│   ├── registry.py     # camera discovery by room or required capability
│   └── actuation.py    # bridges a CameraManifest into an actuatable DeviceManifest
├── perception/
│   ├── fusion.py        # combine independent sensor readings (noisy-OR), no vision model
│   └── observation.py   # ObservationProvider contract: any perception source, one shape
├── discovery/
│   ├── models.py        # DiscoveredDevice -- a candidate, never authority
│   ├── provider.py       # DiscoveryProvider contract + local fixture
│   └── enrollment.py     # enroll_device(): the only path to a DeviceManifest
├── execution/
│   └── registry.py     # ExecutionAdapter protocol + provider_id -> adapter routing
├── integrations/
│   ├── home_assistant/
│   │   ├── adapter.py  # protocol plus local fixture adapter
│   │   ├── client.py   # live REST adapter
│   │   ├── observer.py # one-shot fetch -> WorldSnapshot assembly
│   │   └── state.py    # maps HA state dicts onto DeviceState evidence
│   ├── ir/
│   │   └── adapter.py  # IR-blaster ExecutionAdapter + local fixture
│   ├── wifi/
│   │   └── ssdp.py      # real SSDP (UPnP) DiscoveryProvider, stdlib only
│   └── bluetooth/
│       ├── native.py    # ctypes ABI binding -- only file that touches ctypes.CDLL
│       └── provider.py  # BluetoothProvider (DiscoveryProvider + ExecutionAdapter)
├── audit/
│   └── receipts.py     # machine-readable action receipts
├── web/
│   ├── serialize.py         # domain -> JSON wire format (receipt conventions)
│   ├── application.py       # composition root: build_application() reads setup, builds demo or real
│   ├── haven_application.py # HavenApplication: the production controller (chat, voice, scheduling, persistence)
│   ├── demo.py              # SimulatedHouse + DemoDirector(HavenApplication): fixture world, real engine
│   ├── voice_runtime.py     # composes a real SpeechService / standalone TTS from assigned models + native audio
│   ├── rules_persist.py     # rules.json: automations survive a restart
│   ├── history_persist.py   # HistoryStore: events/actions/receipts/memory survive a restart (SQLite)
│   ├── setup_config.py      # persisted installation config (provider, household_id, feature flags)
│   ├── setup_service.py     # the setup wizard: connect a provider, declare people/contexts, enroll devices
│   ├── server.py            # stdlib HTTP + SSE, static UI, 127.0.0.1 only
│   └── static/              # zero-build desktop/tablet UI (Navigation | World | HAVEN)
├── desktop/
│   ├── shell.py              # native host: loopback server + Edge app window + session
│   └── folder_picker.py      # Windows native folder selection seam
└── runtime.py          # narrow orchestration of the vertical slice
```

The package does not import from a web or API layer. Integrations receive a
command only after the authority engine returns `ALLOW`. The intelligence
provider
returns a `RuleDraft`; it has no reference to the adapter or the store.

Outside `haven/`, `native/haven-bt/` holds the HAVEN-BT C ABI header
(`include/haven_bt.h`) that `haven/integrations/bluetooth/native.py` binds
against, a deterministic fixture implementation
(`src/fixture/fixture_backend.c`), and a real Windows/WinRT platform
backend (`src/platform/windows/winrt_backend.cpp`) verified against actual
Bluetooth hardware. See `native/haven-bt/README.md` for exactly what does
and does not exist there, including Linux/macOS.

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
  + `trigger_room_id`), a `PredictionTrigger` (`event`, `min_confidence`,
  optional `subject_id`), or a `ScheduleTrigger` (`time_of_day`, `weekdays`,
  `window`) -- predictions and schedules become inputs to routines through an
  explicit, owner-approved rule, never as an unrestricted AI decision.
  `required_context` layers on top of *any* trigger kind rather than being a
  fourth one, so "every weekday at 6:30, warm the downstairs, unless nobody's
  home" is a `ScheduleTrigger` plus `required_context="someone_home"`, not a
  new concept. `ScheduleTrigger.is_due(at)` is a pure, stateless check --
  Haven Core has no scheduler daemon and does not track whether a schedule
  already fired today (the same way it has no polling loop for
  `HomeAssistantObserver`); avoiding a duplicate run within one window is a
  caller's job. A
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
  the rule itself declares. Camera/vision/thermal recording, retention,
  stream health, and event clips are not part of this repo: `AlertEngine` is
  the policy layer real observations feed once those exist.
- `haven/cameras` discovers cameras as flat hardware booleans
  (`CameraManifest`/`CameraCapabilities`: `live_stream`, `ptz`,
  `optical_zoom`, `microphone`, `speaker`, `infrared_mode`,
  `privacy_shutter`, `recording`), the same way `haven/devices` discovers
  actuators. `camera_to_device_manifest()` is the bridge from there into
  actuation: it turns declared `ptz`/`optical_zoom`/`privacy_shutter`
  hardware into ordinary `CapabilityDescriptor`s (`pan_tilt`, `zoom`,
  `privacy_shutter`) on a `DeviceManifest`, so a camera runs through the
  exact same `AuthorityEngine`/`ExecutionProviderRegistry` pipeline as any
  other device -- no camera-specific runtime code exists. Engaging or
  disengaging `privacy_shutter` is `ControlClass.GUARDED`, not `LOW_RISK`
  like pan/tilt/zoom: an automated rule cannot toggle the one hardware
  guarantee behind "this camera cannot see us right now" without
  confirmation. Recording start/stop, retention, and event clips are still
  not part of this bridge -- those need a storage/retention model this repo
  does not have. A provider that finds a camera on the network registers it
  with `CameraRegistry`; nothing here performs ONVIF/RTSP/NVR discovery
  itself.
- `haven/integrations/ir` is `FixtureIrBlaster`, the IR-controlled-device
  analogue of `FixtureHomeAssistant` -- old TVs, window AC units, fans, and
  projectors that only accept a learned IR code, not a queryable API. It
  satisfies the same `ExecutionAdapter` shape and registers under its own
  `provider_id`; a real IR blaster (Broadlink, ESPHome IR, LIRC) is a drop-in
  replacement.
- `haven/perception/fusion.py` combines independent readings of the same
  fact (a camera's person-detection confidence, a thermal sensor's
  warm-body confidence) into one `PresenceState`/`ContextState`, using
  noisy-OR (`1 - product(1 - confidence_i)`) -- the standard combination for
  independent evidence of one claim. Sources that disagree on the fact
  itself raise `SensorDisagreement` rather than being resolved by a
  majority vote or highest-confidence-wins policy this module would
  otherwise have to invent. There is no real vision, thermal, IR, BLE, or
  WiFi provider in this repo; this is what any real one's output would be
  combined through, via the single `ObservationProvider.observe() ->
  tuple[PresenceState | ContextState | DeviceState, ...]` shape every
  perception source implements -- Haven never special-cases which transport
  an observation came from.
- Discovery produces a candidate, never authority: a `DiscoveredDevice`
  (from `haven.discovery`) carries only what a scan can know --
  `suggested_device_type`, `suggested_room`, `signal_strength` -- and there
  is no path from it to a controllable `DeviceManifest` except
  `enroll_device()`, which requires a non-empty `approved_by` and
  `justification` and takes `capabilities` from the household, never from
  the candidate's own suggestion. This is what makes Bluetooth/WiFi/mDNS/SSDP
  "device appeared on the network" incapable of becoming "Haven may control
  it" without a deliberate human decision in between.
- `haven.integrations.wifi.ssdp` is a real `DiscoveryProvider`: it sends a
  standard UPnP M-SEARCH multicast and parses whatever responds --
  `SsdpDiscoveryProvider.discover()` is the only method that touches a
  socket, and `parse_ssdp_response()` (what tests exercise, with fixed
  response text) is pure. It only ever returns `DiscoveredDevice`s under
  `provider_id="wifi-ssdp"`; SSDP's `ST`/`USN` headers give a naming-pattern
  guess at `suggested_device_type` (e.g. `ZonePlayer` -> `"zoneplayer"`),
  never a capability -- SSDP cannot tell Haven what an M-SEARCH response
  can actually do, only that something answered. mDNS/SSDP-for-non-UPnP/
  HTTP-vendor-specific/ESPHome/Matter-IP discovery are not part of this
  repo yet.
- Bluetooth is a native ABI (`native/haven-bt/include/haven_bt.h`), not a
  Python Bluetooth library -- there is deliberately no Bleak, or any other
  Bluetooth package, anywhere in `pyproject.toml`. The header **compiles
  clean** (`-Wall -Wextra`, zero warnings) with `llvm-mingw` on Windows, and
  `native/haven-bt/src/fixture/fixture_backend.c` is a real, deterministic
  implementation of the full ABI -- not a platform backend, the native-code
  equivalent of `FixtureHomeAssistant`. `haven/integrations/bluetooth/`
  splits cleanly along the one line that matters: `native.py` is the only
  file that imports `ctypes.CDLL`, and `tests/test_bluetooth_fixture_backend.py`
  compiles the fixture backend and runs `CtypesBluetoothLibrary` against the
  real resulting `.dll`/`.so` (a real polled `HB_EVENT_DEVICE_FOUND`, a real
  GATT write/read round-trip through native memory) -- it skips cleanly
  wherever no C compiler is on `PATH`, so it never blocks the rest of the
  suite. `provider.py`'s `BluetoothProvider` (a `DiscoveryProvider` and
  `ExecutionAdapter`) has no ctypes dependency at all, and is proven end to
  end -- discover -> `enroll_device()` -> `execute()` -- against that same
  real compiled library in the same test file, alongside unit-level
  coverage against a plain-Python `FixtureBluetoothLibrary`. Device identity
  reuses `enroll_device()` unchanged: a candidate's `candidate_id` is the
  native `hb_device_id` as a decimal string, which `enroll_device()` already
  carries straight through to `DeviceManifest.device_id`, so `execute()`
  recovers the native handle with `int(target_device_id)` -- no separate
  id-mapping table exists. `CapabilityDescriptor.service` for a Bluetooth
  capability is the GATT characteristic UUID; `DeviceCommand.parameters`
  must already carry the raw bytes under `"bytes"`, because encoding
  something like `brightness=50` into those bytes is a device-profile
  plugin's job, a layer above this one, not built here. **The Windows
  platform backend is real**: `native/haven-bt/src/platform/windows/winrt_backend.cpp`
  implements adapter enumeration, scan, connect/pair/forget, GATT read/
  write/notify against actual `Windows.Devices.Bluetooth` WinRT APIs, built
  with MSVC + the Windows SDK (`build_windows.cmd`) and verified against
  real hardware -- a real compiled DLL returned genuine nearby BLE
  advertisements through `CtypesBluetoothLibrary` and then through
  `BluetoothProvider.discover()`, indistinguishable from any other
  provider's `DiscoveredDevice`s. `tests/test_bluetooth_winrt_backend.py`
  compiles and loads it on every test run (skipped without MSVC), and
  deliberately asserts nothing about scan *results* -- what's nearby and
  whether a radio is on are facts about a host machine, not this code.
  Linux/BlueZ and macOS/CoreBluetooth are still not built: each needs its
  own SDK and hardware (a running `bluetoothd`, or Xcode + real Apple
  hardware) that this session does not have -- the Windows backend is the
  bar they're held to before being called done. See
  `native/haven-bt/README.md`.
- `HavenRuntime` routes command execution by a device's `DeviceManifest.provider_id`,
  not to one fixed adapter. `HavenRuntime.execution_providers`, an
  `ExecutionProviderRegistry`, maps `provider_id -> ExecutionAdapter`; a
  device with a registered manifest routes there, and everything else
  (no manifest, or `execution_providers` never configured) falls back to
  `home_assistant`, the original single-adapter constructor argument. A
  device whose declared `provider_id` has no registered adapter fails into a
  normal `execution_failed` receipt (`UnknownExecutionProvider` in
  `device_result.detail`), the same as an HTTP failure -- never a raised
  exception. `HomeAssistantAdapter` is exactly `ExecutionAdapter`: Home
  Assistant is one registered provider, not a protocol Haven is specially
  aware of. Constructing `HavenRuntime` with neither `home_assistant` nor
  `execution_providers` raises immediately.
- `haven/speech` is the public speech contract, not a speech engine: events
  (`WakeEvent`, `TranscriptPartial`, `TranscriptFinal`, `SpeakerClaim`, ...)
  and provider protocols (`WakeDetector`, `Vad`, `SpeechRecognizer`,
  `SpeechSynthesizer`) that any implementation — a fixture, a Rust KWS, an
  inference provider from another repo — can satisfy without HAVEN knowing
  who built it. Three invariants are structural, not conventional: a session
  cannot execute from a partial transcript (partials are presentation only);
  a `SpeakerClaim` enters the world as confidence-barred evidence, because
  voice match is not permission; and raw audio is discarded when a session
  closes unless the household explicitly opts to retain it. Echo
  cancellation belongs to the future audio-device layer (it needs the exact
  speaker PCM reference), never to an ASR provider. The wake-word model is
  an external artifact bound through a narrow seam: `KwsModelManifest`
  declares the contract (sample rate, frame, window, threshold, debounce,
  version) and a scorer maps audio to P(positive) — a trained HAVEN-KWS
  served by any inference stack plugs in as that scorer, and
  `haven/speech/providers/` holds thin HTTP adapters for households that
  score wake/ASR/TTS remotely. A dead inference endpoint raises
  `InferenceUnavailableError` and is treated as evidence-unavailable,
  never as a transcript; adapters are constructed and registered by the
  deployer, never in the zero-config defaults. Loaded model handles adapt
  to the speech protocols through `haven/speech/shims.py` (ASR/TTS/wake,
  capability-gated with typed errors), and `SpeechService` is the always-on
  runtime — capture source → wake → VAD → ASR → the invariant-enforcing
  session — with barge-in that stops playback without a model call. The
  browser displays speech state; this service owns the microphone runtime,
  and on Windows that runtime is real: `haven/speech/native_audio.py` binds
  WinMM directly for real microphone capture and speaker playback (no
  third-party audio package), verified against real hardware, with a
  double-buffered playback path so a synthesizer's chunk boundaries never
  starve the device into an audible gap. `haven/web/voice_runtime.py` wires
  a household's assigned wake/ASR/TTS models into a real, running
  `SpeechService`, degrading structurally (never a crash) to the browser's
  simulated voice session wherever a model, the audio device, or the
  platform binding isn't available. Echo cancellation (it needs the exact
  speaker PCM reference) and Linux/macOS native audio backends are the
  remaining native device gaps.
- Receipts distinguish the requested action, derived interpretation, observed
  evidence, authority decision, execution attempt, and device result.

## Run the surface

For the primary Windows desktop host, install the package in the environment
and run:

```powershell
python -m haven.desktop
```

The repository launcher (`run-haven.bat`) uses this desktop entry point too.
Double-clicking it or running it from PowerShell opens HAVEN in its own Edge
app window. Pass `--demo` only when you explicitly want the simulated
household.

This starts HAVEN on an ephemeral loopback port and opens the existing
renderer in an Edge app window. The desktop launch gets a fresh per-launch
session cookie; the local API is not usable without that cookie. The shell
also advertises the `folder_picker` host capability. The same native picker
can choose the installation data directory or a computer-provider root; both
selections go through the existing setup and `FilesystemProvider` boundaries.

Computer access starts read-only: selecting folders permits indexing and
search, not file mutation. The setup wizard exposes file organization as a
separate explicit capability; enabling it still does not bypass HAVEN's
authority or approval path.

The renderer is still available directly when developing or hosting the same
surface elsewhere:

The desktop host can also stay resident without opening a window. This is the
mode used by the optional Windows logon task; it owns the same installation
lock as the windowed host, so a second HAVEN process cannot open the same data
directory concurrently:

```powershell
python -m haven.desktop --background --port 8080
```

For renderer/API development without the desktop shell, run the local web
surface explicitly:

```powershell
python -m haven.web.server --port 8080
```

Then open `http://127.0.0.1:8080/`. The server binds loopback only and has
no desktop session window; use `--demo` explicitly if you need the simulated
household and its scenario controls. A normal web-server launch builds the
real local HAVEN installation, just without the native desktop shell.

## Run the tests

From this directory:

```powershell
python -m pytest -v
```

The test suite is the current executable contract. It uses no network, model
weights, credentials, or household data.

## Verify before you call something a release candidate

There is no paid CI for this repository. `scripts/verify.py` is the local
substitute -- the same command a contributor and a maintainer both run,
producing the same report:

```powershell
python scripts/verify.py --json
```

It runs the full suite and reports Haven's commit, the Python version and OS
that ran it, and total/passed/failed/skipped counts, splitting skips into
"this host lacks a real device, toolchain, or optional runtime package"
(expected on most machines -- native audio hardware, a C compiler for the
Bluetooth native-library tests, `transformers`/`llama_cpp`/`onnxruntime`)
from anything else, which is worth a second look. `--json` also writes
`verification.json` (gitignored -- a local run record, not a repo artifact).
The development rule this exists to support: **a commit is not a release
candidate unless `verify.py` passes.**

The confirmation object in this fixture is a local protocol value, not a
cryptographic credential issuer. A live interface must add secure issuance and
storage before any real high-consequence integration is enabled.

## Authoring a model or provider

Anyone can publish a model manifest, write an inference backend, or
implement one of HAVEN's provider protocols without touching Haven Core
itself -- that seam is the whole point of `haven/models` and
`haven/providers`. Two guides cover it:

- [`docs/authoring-models.md`](docs/authoring-models.md) -- publishing a
  `haven-model.json` manifest so a household can install your model, and
  writing a `ModelBackend` when no existing backend can load it.
- [`docs/authoring-providers.md`](docs/authoring-providers.md) --
  implementing `IntelligenceProvider`, `ExecutionAdapter`,
  `DiscoveryProvider`, `ObservationProvider`, or one of the speech
  protocols, and registering it in a `CapabilityRegistry`.

## Relationship to the adjacent stack

HAVEN is intentionally independent of the existing repositories at this
stage. A future integration should exchange versioned, provenance-preserving
artifacts rather than import private internals across project boundaries:

- Ghost Teacher can generate adversarial household situations and evaluate an
  intelligence provider or planner against them.
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
