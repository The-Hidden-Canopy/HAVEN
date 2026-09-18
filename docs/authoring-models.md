# Authoring a model for HAVEN

This is for two different people, covered in two sections below:

- someone **publishing a model** so a household can install it (you write a
  `haven-model.json` manifest and host it somewhere `install_from_url` can
  reach);
- someone **writing an inference backend** so a new runtime (a new ML
  framework, a new native library, a new remote protocol) can load models at
  all (you implement `ModelBackend`).

Most authors only need the first section. You need the second only if no
existing backend (`http`, `transformers`, `llama_cpp`, `onnx`) can load your
model's file format or serving protocol.

Everything here lives in `haven/models/`. Nothing in this package is
optional infrastructure a deployment can skip: every model family --
intelligence, speech, vision, embeddings, prediction, specialized -- enters
HAVEN through the exact same manifest and the exact same lifecycle. There is
no separate fast path for "official" models; a community publisher's
manifest and Hidden Canopy's own manifests are read by identical code.

## The one rule that shapes everything else

`ModelManager.resolve(kind, requires, languages)` -- the call HAVEN's
runtime actually makes when it needs a model -- routes **only** by `kind`
(the closed family: `intelligence` / `speech` / `vision` / `embeddings` /
`prediction` / `specialized`), by the `capabilities` your manifest declares,
and optionally by `languages`. It never looks at your model's name,
architecture, or backend. A qwen checkpoint running on `transformers` and a
gguf build of something else are the same slot to HAVEN if they declare the
same capabilities. This means:

- **Capabilities are your model's real public interface.** Declare exactly
  what it can do (`chat`, `structured_intent`, `wake_word`, `asr`, `vad`,
  `tts`, `embedding_text`, `object_detection`, ...), because that is the only
  thing a caller can select on.
- **Don't invent a capability name to look more impressive.** If HAVEN or a
  provider protocol doesn't check for it, declaring it does nothing except
  mislead whoever reads your manifest later.
- There is no ranking, licensing tier, or "recommended model" concept in
  Haven Core at all. `haven/models` answers "does something matching this
  request exist," never "which one is best."

## The manifest: `haven-model.json`

One schema (`schema_version: "haven-model-1"`, `haven/models/manifest.py`)
covers every model family. This is the entire artifact you publish -- it
never contains model weights itself, only metadata and pointers to them.

```json
{
  "schema_version": "haven-model-1",
  "id": "acme-chat-7b",
  "version": "1.0.0",
  "kind": "intelligence",
  "capabilities": ["chat", "structured_intent"],
  "architecture": "llama",
  "backend": "llama_cpp",
  "files": {
    "weights": "acme-chat-7b.Q4_K_M.gguf",
    "tokenizer": "tokenizer.json"
  },
  "sha256": {
    "acme-chat-7b.Q4_K_M.gguf": "5f3b...",
    "tokenizer.json": "91ab..."
  },
  "languages": ["en"],
  "hardware": { "cpu": true, "cuda": true },
  "license": "apache-2.0",
  "source": "https://example.org/acme-chat-7b",
  "endpoint": null
}
```

Field notes, from `ModelManifest` and `ModelManifest.from_dict`:

- **`id`** must pass `safe_model_id`: a flat lowercase slug
  (`^[a-z0-9][a-z0-9._-]{0,63}$`), no path separators, no `..`. It becomes a
  storage directory name, so anything path-shaped is rejected outright
  rather than sanitized -- fix the id, don't work around the check.
- **`kind`** is one of the six closed `ModelKind` values above. There is no
  seventh value and no way to add one without changing Haven Core itself.
- **`capabilities`** must be non-empty. This is the open vocabulary; add a
  new capability string freely, but only if some provider protocol or
  runtime call actually checks for it (see `authoring-providers.md`).
- **`files`** maps a role name (`weights`, `tokenizer`, `config`, anything
  you need) to a **relative basename only** -- no subdirectories, no
  absolute paths, no `..`. Storage installs everything flat; a nested path
  is a `ManifestError` naming the offending value, not a silent
  reinterpretation.
- **`sha256`** is all-or-nothing: once you declare *any* hash, every file in
  `files` must have one. A manifest with zero hashes is still legal -- it
  installs with `verified=False` and HAVEN reports hash verification as
  unavailable rather than pretending to have checked. If you want your
  model to show as verified in the UI, hash every file.
- **`hardware`** is a map of device name to boolean; only the `true` entries
  survive into `device_support`. This is a hint for a human deciding what to
  install, not something HAVEN enforces at load time.
- **`endpoint`**, when set, must be `http://` or `https://`. A manifest can
  be file-based (`files` populated, `endpoint` null), endpoint-based
  (`endpoint` set, `files` can be empty), or both (local tokenizer/config
  files plus a remote inference endpoint -- a legitimate topology).
- **`backend`** must name a backend the household actually has registered
  (see the reference set below). This is the one field that is *not*
  capability-routed: `resolve()` never looks at it, but *loading* the
  resolved model does, via `BackendRegistry.resolve(descriptor.backend)`. A
  manifest naming an unregistered backend installs fine and sits at
  `READY`; it only fails, explicitly, as `BACKEND_MISSING` when someone
  tries to load it.

### Getting your manifest into a household

There are exactly three entry paths (`ModelManager`), and every model family
uses the same three -- there is no fourth path and no family-specific
shortcut:

1. **Download** -- `install_from_url(url)` (or `inspect_url(url)` first,
   which never downloads weights, only fetches and validates the manifest).
   Host `haven-model.json` at a stable URL; a resident pastes that URL, or a
   curated catalog entry points at it (see below).
2. **Load Local** -- `install_local_folder(path)`. If your model ships as a
   folder without a manifest, `haven/models/detect.py` recognizes ordinary
   layouts (a lone GGUF, a Hugging Face checkout, an ONNX export) and
   synthesizes one; you don't have to hand-write `haven-model.json` for
   these common shapes, but writing one explicitly is always more precise.
3. **Register External** -- `register_endpoint(endpoint_url, manifest_url=...)`.
   For a model you serve yourself (your own inference server, a hosted API),
   point at that endpoint; if you don't supply a manifest URL, HAVEN
   synthesizes a minimal one and marks it unverified.

**Inspect-before-install is structural, not a UI courtesy**: `inspect_url`
fetches and validates *only* the manifest bytes. No download of `files`
happens until a caller explicitly proceeds past inspection. Don't build
around this by, say, redirecting the manifest URL to something that starts
a download as a side effect -- that defeats the guarantee the whole flow
exists to give a household.

### Publishing a catalog (optional)

If you're distributing several models, a catalog file
(`haven/models/catalog.py`) is a flat JSON list (or `{"entries": [...]}`) of
pointers, never models themselves:

```json
[
  {
    "id": "acme-chat-7b",
    "kind": "intelligence",
    "manifest_url": "https://example.org/models/acme-chat-7b/haven-model.json",
    "description": "7B chat model, Q4 quantized",
    "size_hint_mb": 4200,
    "license": "apache-2.0"
  }
]
```

`manifest_url` must be `http(s)`. HAVEN Core ships **no** built-in catalog
(`default_catalog()` returns nothing) -- a household or a community
publisher supplies their own catalog file; there is no submission process
to get "listed" in Haven Core itself, because Haven Core doesn't maintain a
list.

### The lifecycle your manifest moves through

`ModelState` (`haven/models/states.py`): `DISCOVERED -> INSPECTED ->
REGISTERED -> VERIFIED -> READY -> LOADED`, with named failure states that
never collapse into a generic "unavailable":

| State | Meaning |
|---|---|
| `INCOMPLETE` | manifest references a file that isn't present |
| `UNSUPPORTED` | manifest schema/kind HAVEN doesn't recognize |
| `HASH_MISMATCH` | bytes on disk don't match the declared sha256 |
| `LICENSE_UNKNOWN` | no license declared and the deployment requires one |
| `BACKEND_MISSING` | no loader plugin registered for `backend` |
| `LOAD_FAILED` | the backend's `load()` raised |
| `UNREACHABLE` | an endpoint didn't answer at registration time |

If something is wrong with your manifest, it will surface as one of these
specific states, not a bare error string -- when debugging an install that
didn't work, check `ModelRecord.state`, not just an exception message.

## Writing a `ModelBackend`

Only needed when no existing backend can load your model. The entire
contract (`haven/models/backends/protocol.py`) is two methods:

```python
from pathlib import Path
from haven.models.contracts import ModelDescriptor

class LoadedModel(Protocol):
    @property
    def descriptor(self) -> ModelDescriptor: ...
    def unload(self) -> None: ...

class ModelBackend(Protocol):
    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> LoadedModel: ...
```

`load()` receives the normalized `ModelDescriptor` (never the raw manifest)
and, for file-based models, the directory their `files` were installed
into. It returns a `LoadedModel` handle -- whatever object your backend
needs internally, as long as it exposes `descriptor` and `unload()`.
Everything past that (a `chat()` method, a `transcribe()` method, whatever
your model kind needs) is your handle's own API; HAVEN's manager only ever
calls `load` and `unload` on it.

Register your backend by name:

```python
from haven.models.backends.protocol import BackendRegistry

registry = BackendRegistry()
registry.register("acme-runtime", AcmeModelBackend())
```

`resolve()` on an unregistered name returns `None`, never raises -- this is
what lets the manager report the explicit `BACKEND_MISSING` state instead of
crashing. Match that contract in anything you build on top: a missing
capability should become a named, inspectable state, not an exception a
caller has to catch blind.

### Study the reference backends before writing your own

`haven/models/backends/`:

- **`http.py`** (`HttpModelBackend`) -- fully real, stdlib-only, always
  active. This is the backend to read first: it's the shortest complete
  example of the contract. It loads `ENDPOINT` descriptors and speaks a
  minimal JSON envelope (`POST {endpoint}/chat`, `/complete`,
  `/{capability_method}`), normalizing several response shapes (a bare
  `{"text": ...}`, OpenAI-chat-ish `choices[0].message.content`,
  completion-ish `choices[0].text`, or a bare JSON string) into one
  `ChatResult`/`InferenceResult` before HAVEN ever sees output. Anything it
  can't recognize becomes a `BackendConnectionError` naming the shape --
  never a guess. If you're wrapping a remote HTTP API, extend or mirror this
  one rather than starting from scratch.
- **`transformers_backend.py`**, **`llama_cpp_backend.py`**,
  **`onnx_backend.py`** -- lazy backends: each only activates when its
  runtime is actually importable in the environment, and reports
  `BACKEND_MISSING` otherwise rather than a raw `ImportError` reaching a
  caller. If your backend depends on an optional third-party package,
  follow this pattern: try the import inside your backend module, and let
  registration fail soft (or simply don't register) when it's absent,
  rather than making the import a hard dependency of `haven/models` itself.
- **`reference.py`** -- `reference_backends()` assembles the shipped set and
  `BackendConnectionError`, the shared error type reference backends raise
  for a reachability/response problem. Reuse this type for the same class of
  failure in your own backend so callers can catch one thing.

Community backends (mlx, rocm, coreml, or your own vendor runtime) register
exactly the same way as the reference set -- there is no separate
registration path for "official" versus "third-party" backends.

## Checklist before you publish

- [ ] `id` is a flat lowercase slug, not a path.
- [ ] `capabilities` names things a real provider protocol or runtime call
      checks for (see `authoring-providers.md`), not aspirational labels.
- [ ] Every file in `files` is a relative basename; if you hash one file,
      hash all of them.
- [ ] `backend` names a backend the target deployment will actually have
      registered -- or ship your own `ModelBackend` alongside the manifest.
- [ ] You've run `inspect_url` (or the equivalent local check) yourself
      before telling anyone else to install from your URL.
- [ ] If you're publishing an endpoint model, the endpoint answers requests
      in the shape your chosen backend expects (see `http.py` above for the
      shapes the reference HTTP backend accepts).
