# Authoring a provider for HAVEN

A "provider" in HAVEN is anything that plugs into one of a small set of
`typing.Protocol` contracts so that a real integration -- an LLM, a camera
feed, a BLE scanner, a smart-home hub, a wake-word model -- can sit behind
the same boundary as HAVEN's own fixtures, indistinguishable to the runtime
that calls it. This is deliberate: `haven/providers` answers "is there a
provider of kind X that supports capability Y," never "which vendor backs
it" and never "is this the licensed one." A free fixture and a paid Hidden
Canopy provider satisfy the exact same protocol.

Every protocol below lives in Haven Core with **zero** third-party
dependencies. Nothing here requires you to import from `haven/providers` or
`haven/models` to implement one -- most protocols need only the dataclasses
they're typed against (`haven.core.domain`, `haven.speech.events`, etc.).
Read the module docstring at the top of each contract file before writing
against it; each one explains *why* the boundary is drawn where it is, and
that reasoning is often the difference between a provider that works and
one that quietly breaks an invariant a test won't catch.

## Before you start: the one boundary that matters everywhere

**A provider proposes or observes. It never decides, and it never
executes.** Concretely:

- An `IntelligenceProvider` can draft a rule, answer a question, or explain
  a decision -- it cannot create a rule, approve one, or command a device.
- An `ObservationProvider` or `DiscoveryProvider` can report what it sees --
  it cannot turn that sighting into a controllable device or an authorized
  action.
- An `ExecutionAdapter` only runs a command `AuthorityEngine` has *already*
  allowed. It never sees a request before authority has decided on it.

If you find yourself wanting your provider to skip a step -- have your LLM
call a device directly, have your scanner auto-enroll a device it finds, have
your backend cache a "yes" so authority doesn't have to re-check -- that's a
sign you're about to reintroduce the exact failure mode this architecture
exists to prevent. Route around the urge, not around the boundary.

## `IntelligenceProvider` — proposal-only intelligence

`haven/intelligence/gateway.py`. Selected through the capability registry by
`kind="intelligence"` and the capabilities a call needs (e.g. `{"interpret"}`
or `{"chat", "structured_intent"}`); HAVEN never asks which vendor or repo
backs it. The zero-config default is `ScriptedIntelligenceProvider` -- HAVEN
runs with no model-backed provider installed at all.

```python
class IntelligenceProvider(Protocol):
    def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft: ...
    def chat(self, context: AgentContext, message: str) -> AgentResponse: ...
    def propose_rule(self, context: AgentContext, message: str) -> RuleDraft: ...
    def explain(self, context: AgentContext, decision: AuthorityDecision) -> AgentResponse: ...
    def interpret_intent(self, text: str, *, context: AgentContext, principal: Principal, now: datetime) -> Intent: ...
```

Every method returns something inert: a `RuleDraft` a human still has to
approve, an `AgentResponse` that's plain text, or an `Intent` union
(`QueryRequest | ActionProposal | RuleDraft | ClarificationRequest |
ConversationMessage`) that HAVEN itself routes based on which variant came
back. **None of these methods receive the store, a device registry, or an
execution adapter** -- `AgentContext` hands you a bounded, serializable
`WorldView` (freshness and confidence per item) instead of a window into
live state, on purpose. If your implementation needs more household context
than `AgentContext` carries, that's a sign to extend `AgentContext` (a repo
change, discussed with the maintainers) rather than to reach around it by
importing `haven.core.store` directly from your provider.

`interpret_intent` is the one method to implement carefully if you want
your provider to handle arbitrary utterances rather than one scripted
phrase: it's the single classification seam every kind of intelligence --
LLM, deterministic planner, ensemble -- proposes through, and HAVEN routes
uniformly from there (a query answers from the world view, an action
proposal crosses to authority through the direct path, a rule draft enters
the propose/approve lifecycle, a clarification returns to the human). Look
at `DeterministicIntentInterpreter` (`haven/intelligence/interpreter.py`)
for the zero-model reference classification and at
`ScriptedIntelligenceProvider` for how a provider wires its own `interpret`
into that seam.

**Do not** have your provider call an integration, mutate the store, or
return anything with a `target_device_id` that HAVEN would execute without
a human approving it first. If your provider is backed by an LLM, treat
every field it fills in on a `RuleDraft` as *proposed*, including ones that
look unambiguous -- the approval boundary is where a household actually
consents, not a formality your provider can shortcut because it's confident.

## `ExecutionAdapter` — running an already-authorized command

`haven/execution/registry.py`. The entire contract:

```python
class ExecutionAdapter(Protocol):
    def execute(self, command: DeviceCommand) -> DeviceResult:
        """Execute one already-authorized command."""
```

Register under a `provider_id` in an `ExecutionProviderRegistry`; a device's
`DeviceManifest.provider_id` is how `HavenRuntime` finds your adapter for
that device -- a straight 1:1 lookup, not a capability negotiation (that's
what `haven.providers.CapabilityRegistry` is for; don't conflate the two
registries). By the time your `execute()` is called, `AuthorityEngine` has
already returned `ALLOW` for this exact command -- your adapter's only job
is to talk to the real device and report what happened.

**Never** return a fabricated success. `DeviceResult` should reflect what
your integration actually observed happen; a `home_assistant`-shaped
reference (`haven/integrations/home_assistant/`) shows the pattern of a
fixture adapter (records commands locally, deterministic) alongside a real
one (`LiveHomeAssistantAdapter`, real network I/O isolated to
`client.py`). If your device can fail, report the failure as a normal
`DeviceResult` your caller handles -- an `UnknownExecutionProvider`-style
raise is reserved for routing problems (no adapter registered for a
`provider_id`), not for a real device declining a command.

Study `haven/integrations/ir/adapter.py` (`FixtureIrBlaster`) too if your
target device is "send a fixed code, get no queryable state back" rather
than "call a REST API" -- it's the reference shape for that class of
integration (old TVs, ACs, IR-only fans/projectors).

## `DiscoveryProvider` — finding candidates, never authority

`haven/discovery/provider.py`:

```python
class DiscoveryProvider(Protocol):
    def discover(self) -> tuple[DiscoveredDevice, ...]:
        """Return the candidate devices this transport currently sees."""
```

`DiscoveredDevice` (`haven/discovery/models.py`) can only ever carry what a
scan can actually know -- `suggested_device_type`, `suggested_room`,
`signal_strength`. There is exactly one path from a `DiscoveredDevice` to a
controllable `DeviceManifest`: `enroll_device()`
(`haven/discovery/enrollment.py`), which requires a non-empty `approved_by`
and `justification`, and takes `capabilities` from the household's own
declaration, **never** from your provider's suggestion. This is what keeps
"something answered on the network" from becoming "HAVEN may control it"
without a human decision in between.

If you're writing a real scanner (BLE, mDNS, a vendor's own device
directory), the reference to read is `haven/integrations/wifi/ssdp.py`
(`SsdpDiscoveryProvider`): it's a real UPnP M-SEARCH multicast, and it only
ever returns `DiscoveredDevice`s tagged under its own `provider_id`
(`"wifi-ssdp"`). Its `suggested_device_type` comes from a naming-pattern
guess on SSDP's `ST`/`USN` headers (e.g. `ZonePlayer` -> `"zoneplayer"`) --
**never** a capability claim, because an M-SEARCH response can only tell you
something answered, not what it can actually do. Match that honesty in your
own provider: a suggestion field should say what you *guessed*, not what
you're asserting the device supports.

Give your provider its own stable `provider_id` (see
`haven/integrations/bluetooth/provider.py`'s `BluetoothProvider` for a
discovery-plus-execution provider that does this cleanly) so device identity
survives round-tripping through `enroll_device()` without a separate
id-mapping table.

## `ObservationProvider` — perception, one shape for every source

`haven/perception/observation.py`:

```python
Observation = Union[PresenceState, ContextState, DeviceState]

class ObservationProvider(Protocol):
    def observe(self) -> tuple[Observation, ...]:
        """Return whatever this source currently observes."""
```

This is the seam for BLE proximity, WiFi association, motion/door sensors,
camera or thermal detections -- anything that produces evidence about
presence, context, or device state. It's a **flat batch, not a snapshot**:
your provider doesn't know the household's device registry or a snapshot's
validity window, only what it just observed. A caller collects `observe()`
from however many sources are configured and, where two sources describe
the same fact, combines them via `haven.perception.fuse_presence` /
`fuse_context` (noisy-OR: `1 - product(1 - confidence_i)`) before folding
the result into a `WorldSnapshot` -- don't do that combination yourself
inside a single provider; report your own reading and let fusion handle
agreement between independent sources.

**Set `confidence` honestly.** Every `PresenceState`/`ContextState`/
`DeviceState` carries `confidence: float = 1.0`, and
`AuthorityEngine.minimum_confidence` (default `1.0`, fail-closed) uses it to
block low-confidence evidence from authorizing anything. If your provider is
a probabilistic source (vision, IR, anything that isn't a hard binary
sensor), this field is *the* mechanism a household uses to decide how much
to trust you -- reporting `1.0` because it's the default, when your model
is actually uncertain, defeats the entire fail-closed design. If two of your
own readings genuinely disagree about the underlying fact (not just differ
in confidence), let `SensorDisagreement`
(`haven/perception/fusion.py`) surface rather than picking a winner
yourself with a majority vote or highest-confidence-wins heuristic --
Haven Core deliberately doesn't invent that policy, and neither should a
single provider.

## Speech provider protocols

`haven/speech/protocols.py`. Four narrow contracts, all speaking the same
pinned PCM wire format: **16-bit signed little-endian mono, 16 kHz**; one
25 ms frame is 400 samples / 800 bytes. An implementer needs nothing from
HAVEN internals beyond the event dataclasses (`haven/speech/events.py`) and
this format.

```python
class WakeDetector(Protocol):
    def process(self, pcm: bytes) -> list[WakeEvent]: ...

class Vad(Protocol):
    def process(self, pcm: bytes) -> list[SpeechStarted | SpeechContinued | SpeechEnded]: ...

class SpeechRecognizer(Protocol):
    def accept(self, pcm: bytes) -> list[TranscriptPartial | TranscriptFinal]: ...
    def finalize(self) -> list[TranscriptFinal]: ...

class SpeechSynthesizer(Protocol):
    def speak(self, text: str) -> Iterable[bytes]: ...
    def stop(self) -> None: ...
```

Three invariants are load-bearing, not stylistic:

- **`WakeDetector` and `Vad` must be streaming.** Audio arrives as it's
  captured; a detection or segmentation event refers back to where it
  actually occurred, because the utterance has already begun by the time
  your detector fires. Don't buffer everything and batch-process at the
  end -- that breaks the pre-roll ring buffer's whole purpose
  (`haven/speech/ring_buffer.py`).
- **Downstream code consumes only `TranscriptFinal`, never
  `TranscriptPartial`.** `haven/speech/session.py` structurally cannot act
  on a partial -- this is enforced above your provider, but write your
  recognizer as if it weren't: never let a caller mistake an interim
  hypothesis for a committed transcript.
- **`SpeechSynthesizer.stop()` must not require a model or LLM call.** A
  barge-in ("haven, stop") has to silence playback within one chunk. If
  your synthesizer's `stop()` has to round-trip to a remote service before
  audio actually stops, that's a correctness bug in the provider, not an
  acceptable latency trade-off.

For a wake-word model specifically, `KwsModelManifest`
(`haven/speech/wake_model.py`) is the narrow seam: it declares the contract
(sample rate, frame, window, threshold, debounce, version) and your scorer
maps audio to `P(positive)`. You don't need to reimplement wake-word
detection framing yourself -- `ThresholdedWakeDetector` already does the
buffering/threshold/debounce logic around whatever scorer you provide.

If you're serving inference remotely rather than embedding a model in
process, `haven/speech/providers/http_inference.py` is the reference thin
adapter (HTTP seam to a household inference stack) to extend or mirror,
the same way `haven/models/backends/http.py` is the reference for a model
backend.

## Registering your provider

However you implement one of the protocols above, registration is the same
shape everywhere: construct a `ProviderCapabilities` (`provider_id`, `kind`,
and the capability strings your instance actually supports) and register
both together in a `CapabilityRegistry`
(`haven/providers/capabilities.py`):

```python
from haven.providers.capabilities import CapabilityRegistry, ProviderCapabilities

registry = CapabilityRegistry()
registry.register(
    MyIntelligenceProvider(),
    capabilities=ProviderCapabilities(
        provider_id="acme.intelligence",
        kind="intelligence",
        capabilities=("interpret", "chat", "structured_intent"),
    ),
)
```

`haven/providers/defaults.py` (`build_default_registry`) shows this for
every shipped fixture -- copy its shape for your own registration code
rather than reinventing it. Nothing about registration is required at
import time or globally: a deployment builds its own registry and registers
only the providers it actually has, so a household with no vision provider
installed simply never sees `kind="vision"` resolve to anything, rather
than seeing a placeholder.

**Only declare a capability your instance genuinely supports.** The
registry has no probing and no verification step -- `find(kind, requires)`
trusts what you declared. Declaring `"structured_intent"` when your
`interpret_intent` actually only handles one scripted phrase (like the
default fixture does, honestly) will make a caller select you for requests
you can't serve.

## Checklist before you ship a provider

- [ ] Your provider proposes or observes; it never mutates the store,
      creates a rule, or calls an execution adapter directly.
- [ ] Every confidence value you emit reflects real uncertainty, not a
      default you never bothered to change.
- [ ] Your `provider_id` is stable across restarts if device identity or
      capability-matching depends on it.
- [ ] `ProviderCapabilities.capabilities` names only what you actually
      implement -- checked against the specific protocol methods, not
      aspirational feature names.
- [ ] If you're a discovery/observation provider, you've verified there is
      no path in your code from "device seen" to "device controllable"
      that bypasses `enroll_device()`.
- [ ] If you're a speech provider, you've verified `stop()`/finalization
      behavior against the invariants above, not just the happy path.
