"""The ModelManager facade: one router for every model family.

Models are interchangeable local capabilities, not the product, and multiple
models are loaded simultaneously by design (wake + VAD + ASR + vision +
agent + embeddings all live at once): the manager is a capability router,
not single-select. All three entry paths -- Download (curated catalog or
manifest URL), Load Local (folder or model root), Register External
(endpoint) -- are explicit, produce identical record shapes, and every state
transition is persisted. Discovery NEVER auto-activates; `register_candidate`
is the explicit gate, the way enrollment is for devices.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .backends import BackendRegistry, LoadedModel, reference_backends
from .catalog import CatalogEntry
from .contracts import ModelDescriptor, ModelKind, ModelSource, safe_model_id
from .detect import detect_folder, slugify_model_id
from .discovery import DiscoveryResult, scan_roots
from .downloader import (
    HashMismatchError,
    ModelSourceError,
    ResolvedSource,
    UrlInspection,
    download_files,
    inspect_manifest_url,
    resolve_model_url,
)
from .integrity import verify_files
from .manifest import ModelManifest
from .registry import ModelRecord, ModelRegistry
from .states import ModelState
from .storage import ModelStorage, default_models_root


class ModelManagerError(RuntimeError):
    """Base class for expected model-manager failures."""


class ModelNotFoundError(ModelManagerError, LookupError):
    """Raised when no model or no matching model exists."""


class BackendMissingError(ModelManagerError):
    """Raised when no loader plugin is registered for a manifest's backend."""


class ModelLoadError(ModelManagerError):
    """Raised when a backend loader fails to load a model."""


_ROOTS_FILENAME = "model_roots.json"
_ASSIGNMENTS_FILENAME = "assignments.json"
_PROBE_TIMEOUT_SECONDS = 5

# The persisted role vocabulary: which model serves chat / speech / vision.
ROLE_CHAT = "chat"
ROLE_ASR = "asr"
ROLE_TTS = "tts"
ROLE_WAKE_WORD = "wake_word"
ROLE_VAD = "vad"
ROLE_VISION = "vision"

ROLES = (ROLE_CHAT, ROLE_ASR, ROLE_TTS, ROLE_WAKE_WORD, ROLE_VAD, ROLE_VISION)

# What a model must be to serve a role: kind + capabilities, never id or
# backend. Exported so the web layer (and future role-based routing) shares
# the same requirement table.
ROLE_REQUIREMENTS = {
    ROLE_CHAT: (ModelKind.INTELLIGENCE, frozenset({"chat"})),
    ROLE_ASR: (ModelKind.SPEECH, frozenset({"asr"})),
    ROLE_TTS: (ModelKind.SPEECH, frozenset({"tts"})),
    ROLE_WAKE_WORD: (ModelKind.SPEECH, frozenset({"wake_word"})),
    ROLE_VAD: (ModelKind.SPEECH, frozenset({"vad"})),
    ROLE_VISION: (ModelKind.VISION, frozenset({"object_detection"})),
}

# The lazy reference loaders probe their runtime package by name; anything
# else registered is a custom loader, which reports available (there is no
# runtime package to probe, only the loader itself).
_LAZY_RUNTIME_PACKAGES = {
    "transformers": "transformers",
    "llama_cpp": "llama_cpp",
    "onnx": "onnxruntime",
}


@dataclass(frozen=True)
class BackendAvailability:
    """One registered backend's runtime availability probe."""

    backend: str
    available: bool
    detail: str


def _base_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def _basename_keyed(sha256: dict[str, str]) -> dict[str, str]:
    return {Path(rel).name: digest for rel, digest in sha256.items()}


def _looks_like_manifest_url(url: str) -> bool:
    return Path(urllib.parse.urlsplit(url.strip()).path).name == "haven-model.json"


def _synthesize_endpoint_manifest(endpoint_url: str) -> ModelManifest:
    """Minimal metadata for a bare endpoint: no manifest URL was supplied."""

    parsed = urllib.parse.urlsplit(endpoint_url)
    hint = "-".join(part for part in (parsed.hostname, str(parsed.port or ""), parsed.path) if part)
    model_id = safe_model_id(slugify_model_id(hint) or "remote-endpoint")
    return ModelManifest(
        id=model_id,
        version="0.0.0",
        kind=ModelKind.SPECIALIZED,
        capabilities=frozenset({"inference"}),
        architecture="remote",
        backend="http",
        files={},
        sha256={},
        license=None,
        endpoint=endpoint_url,
        source=f"endpoint:{endpoint_url}",
    )


class ModelManager:
    def __init__(
        self,
        models_root: str | Path | None = None,
        *,
        catalog: tuple[CatalogEntry, ...] = (),
        backends: BackendRegistry | None = None,
    ) -> None:
        self.models_root = Path(models_root) if models_root is not None else default_models_root()
        self._storage = ModelStorage(self.models_root)
        self._registry = ModelRegistry(self.models_root / "registry.json")
        self._backends = backends if backends is not None else reference_backends()
        self._catalog = tuple(catalog)
        self._loaded: dict[str, LoadedModel] = {}
        self._roots_path = self.models_root / _ROOTS_FILENAME
        self._roots: list[str] = self._load_roots()
        self._assignments: dict[str, str] = self._load_assignments()

    def _load_roots(self) -> list[str]:
        if not self._roots_path.exists():
            return []
        try:
            data = json.loads(self._roots_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return [str(item) for item in data] if isinstance(data, list) else []

    def _save_roots(self) -> None:
        self.models_root.mkdir(parents=True, exist_ok=True)
        self._roots_path.write_text(json.dumps(self._roots, indent=2) + "\n", encoding="utf-8")

    def _load_assignments(self) -> dict[str, str]:
        path = self.models_root / _ASSIGNMENTS_FILENAME
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(role): str(model_id)
            for role, model_id in data.items()
            if role in ROLE_REQUIREMENTS and isinstance(model_id, str) and model_id
        }

    def _save_assignments(self) -> None:
        self.models_root.mkdir(parents=True, exist_ok=True)
        payload = {role: self._assignments[role] for role in ROLES if role in self._assignments}
        path = self.models_root / _ASSIGNMENTS_FILENAME
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # -- entry path: Download -------------------------------------------------

    def search(self, query: str = "") -> tuple[CatalogEntry, ...]:
        """Filter the curated catalog by id/description/kind substring."""

        needle = query.strip().lower()
        if not needle:
            return self._catalog
        return tuple(
            entry
            for entry in self._catalog
            if needle in entry.id.lower()
            or needle in entry.description.lower()
            or needle in entry.kind.value
        )

    def inspect_url(self, url: str) -> UrlInspection:
        """Fetch and parse a manifest URL only; never downloads weights."""

        return inspect_manifest_url(url)

    def install_from_url(self, url: str, *, replace: bool = False) -> ModelRecord:
        """Download entry path: resolve -> inspect -> download -> verify -> READY."""

        resolved = resolve_model_url(url)
        return self._install_resolved(resolved, url, replace=replace)

    def _install_resolved(
        self,
        resolved: ResolvedSource,
        source_detail: str,
        *,
        replace: bool = False,
        progress=None,
        cancel=None,
        staging_dir: str | Path | None = None,
        on_downloaded=None,
    ) -> ModelRecord:
        """The shared download->install->verify core behind both entry paths.

        The synchronous `install_from_url` and the background job manager
        both land here so a job-driven install transitions the registry
        exactly like a synchronous one. `staging_dir` overrides the
        temporary download destination (the job path uses a stable per-model
        staging dir so interrupted files stay resumable); `progress` and
        `cancel` are the downloader's byte-progress and cooperative-cancel
        hooks; `on_downloaded` fires after the bytes are on disk and
        hash-verified but before they are installed into storage.
        """

        manifest = resolved.manifest
        self._register(manifest, ModelSource.DOWNLOADED, source_detail)
        try:
            if staging_dir is not None:
                download_files(
                    manifest,
                    base_url=resolved.base_url,
                    dest_dir=Path(staging_dir),
                    progress=progress,
                    cancel=cancel,
                )
                if on_downloaded is not None:
                    on_downloaded()
                dest = self._storage.install(manifest, Path(staging_dir), replace=replace)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    download_files(
                        manifest,
                        base_url=resolved.base_url,
                        dest_dir=Path(tmp),
                        progress=progress,
                        cancel=cancel,
                    )
                    if on_downloaded is not None:
                        on_downloaded()
                    dest = self._storage.install(manifest, Path(tmp), replace=replace)
        except HashMismatchError:
            self._registry.update_state(manifest.id, ModelState.HASH_MISMATCH)
            raise
        except ModelSourceError:
            self._registry.update_state(manifest.id, ModelState.INCOMPLETE)
            raise
        descriptor = manifest.to_descriptor(ModelSource.DOWNLOADED, str(dest), verified=False)
        return self._verify_and_ready(manifest, descriptor)

    # -- entry path: Load Local ------------------------------------------------

    def install_local_folder(self, folder: str | Path, *, replace: bool = False) -> ModelRecord:
        """Local folder entry path: detect -> install -> verify.

        The folder may carry a `haven-model.json` or match a known layout
        (single *.gguf, config.json + weights, *.onnx); either way a manifest
        is required -- an unrecognizable folder is refused before anything
        is registered or copied.
        """

        folder = Path(folder)
        detection = detect_folder(folder)
        manifest = detection.manifest
        if manifest is None:
            problem = "; ".join(detection.problems) or "no model manifest or recognized layout"
            raise ModelManagerError(f"cannot install {folder}: {problem}")
        self._register(manifest, ModelSource.LOCAL, str(folder))
        dest = self._storage.install(manifest, folder, replace=replace)
        descriptor = manifest.to_descriptor(ModelSource.LOCAL, str(dest), verified=False)
        return self._verify_and_ready(manifest, descriptor)

    # -- entry path: Register External -----------------------------------------

    def register_endpoint(self, endpoint_url, *, manifest_url: str | None = None) -> ModelRecord:
        """Endpoint entry path: the ENDPOINT URL is the inference contract.

        `endpoint_url` is probed and recorded as `endpoint_url` on the
        descriptor; the optional `manifest_url` supplies metadata (kind,
        capabilities, architecture, backend). Without one, a minimal
        descriptor (specialized / {"inference"} / remote / http) is
        synthesized. A connection failure at registration time is recorded
        as UNREACHABLE but does not block registration; an HTTP error
        response still counts as reachable (a 404 router is a live
        endpoint). For backward compatibility a `ModelManifest` may be
        passed positionally, and a positional string that names a
        `haven-model.json` is fetched for metadata (the historical
        `add-endpoint` behavior).
        """

        if isinstance(endpoint_url, ModelManifest):
            manifest = endpoint_url
            endpoint = manifest.endpoint
            if endpoint is None:
                if not manifest.source:
                    raise ModelManagerError("endpoint manifest must declare an endpoint or source URL")
                endpoint = _base_url(manifest.source)
            return self._register_endpoint_manifest(manifest, endpoint)
        if not isinstance(endpoint_url, str) or not endpoint_url.strip():
            raise ModelManagerError("register_endpoint expects an http(s) endpoint URL")
        endpoint = endpoint_url.strip()
        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            raise ModelManagerError(f"endpoint URL must be http(s): {endpoint!r}")
        manifest = None
        if manifest_url is not None:
            manifest = self._fetch_endpoint_manifest(manifest_url)
        elif _looks_like_manifest_url(endpoint):
            manifest = self._fetch_endpoint_manifest(endpoint)
        if manifest is None:
            manifest = _synthesize_endpoint_manifest(endpoint)
        return self._register_endpoint_manifest(manifest, endpoint)

    def _fetch_endpoint_manifest(self, url: str) -> ModelManifest:
        return resolve_model_url(url).manifest

    def _register_endpoint_manifest(self, manifest: ModelManifest, endpoint: str) -> ModelRecord:
        descriptor = ModelDescriptor(
            id=manifest.id,
            kind=manifest.kind,
            capabilities=manifest.capabilities,
            backend=manifest.backend,
            architecture=manifest.architecture,
            version=manifest.version,
            source=ModelSource.ENDPOINT,
            path=None,
            endpoint_url=endpoint,
            languages=manifest.languages,
            device_support=manifest.device_support,
            license=manifest.license,
            verified=False,
            files={},
            sha256={},
        )
        record = self._register_descriptor(descriptor, ModelSource.ENDPOINT, endpoint)
        self._storage.install_endpoint(manifest, replace=True)
        if not self._probe(endpoint):
            return self._registry.update_state(descriptor.id, ModelState.UNREACHABLE)
        self._registry.update_state(descriptor.id, ModelState.VERIFIED)
        return self._registry.update_state(descriptor.id, ModelState.READY)

    def _probe(self, url: str) -> bool:
        request = urllib.request.Request(url, headers={"User-Agent": "haven-model-manager"})
        try:
            with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS):
                return True
        except urllib.error.HTTPError:
            return True  # the server answered; unreachable means no answer
        except urllib.error.URLError:
            return False

    # -- discovery -------------------------------------------------------------

    def add_root(self, path: str | Path) -> tuple[str, ...]:
        resolved = str(Path(path))
        if resolved not in self._roots:
            self._roots.append(resolved)
            self._save_roots()
        return self.roots()

    def roots(self) -> tuple[str, ...]:
        return tuple(self._roots)

    def scan(self) -> list[DiscoveryResult]:
        """Classify candidates across all roots; never auto-activates."""

        return scan_roots(self._roots)

    def register_candidate(self, result: DiscoveryResult) -> ModelRecord:
        """Explicit activation of a discovered candidate."""

        if result.manifest is None:
            raise ModelManagerError(f"candidate at {result.path} has no parseable manifest")
        manifest = result.manifest
        self._register(manifest, ModelSource.LOCAL, str(result.path))
        dest = self._storage.install(manifest, result.path)
        descriptor = manifest.to_descriptor(ModelSource.LOCAL, str(dest), verified=False)
        return self._verify_and_ready(manifest, descriptor)

    # -- queries -----------------------------------------------------------------

    def list_models(self, state: ModelState | None = None) -> tuple[ModelRecord, ...]:
        return self._registry.list(state)

    def get(self, model_id: str) -> ModelRecord | None:
        return self._registry.get(model_id)

    def resolve(
        self,
        kind: ModelKind | str,
        *,
        requires: frozenset[str] | set[str] = frozenset(),
        languages: frozenset[str] | set[str] = frozenset(),
    ) -> ModelDescriptor:
        """Best READY-or-LOADED descriptor for kind + capabilities + languages.

        Matching is by kind and capability set only -- NEVER by
        architecture, backend, or id, which are loader metadata. Preference
        order: LOADED first, then newest registered_at.
        """

        kind = kind if isinstance(kind, ModelKind) else ModelKind(kind)
        required = frozenset(requires)
        wanted_languages = frozenset(languages)
        candidates = []
        for record in self._registry.list():
            if record.state not in (ModelState.READY, ModelState.LOADED):
                continue
            if record.kind is not kind:
                continue
            if not record.descriptor.supports_all(required):
                continue
            if wanted_languages and not (record.descriptor.languages & wanted_languages):
                continue
            candidates.append(record)
        if not candidates:
            raise ModelNotFoundError(
                f"no {kind.value} model satisfies requires={sorted(required)} languages={sorted(wanted_languages)}"
            )
        best = max(candidates, key=lambda r: (r.state is ModelState.LOADED, r.registered_at))
        return best.descriptor

    # -- lifecycle ---------------------------------------------------------------

    def load(self, model_id: str) -> ModelRecord:
        record = self._require(model_id)
        descriptor = record.descriptor
        loader = self._backends.resolve(descriptor.backend)
        if loader is None:
            self._registry.update_state(model_id, ModelState.BACKEND_MISSING)
            raise BackendMissingError(f"no backend registered for {descriptor.backend!r}")
        model_dir = (
            None
            if descriptor.source is ModelSource.ENDPOINT
            else self._storage.model_dir(descriptor.kind, descriptor.id)
        )
        try:
            handle = loader.load(descriptor, model_dir)
        except BackendMissingError:
            # A lazy reference backend (transformers / llama_cpp / onnx) raises
            # this when its runtime is not importable. It names a missing
            # dependency, not a failed load -- keep the state explicit.
            self._registry.update_state(model_id, ModelState.BACKEND_MISSING)
            raise
        except Exception as exc:
            self._registry.update_state(model_id, ModelState.LOAD_FAILED)
            raise ModelLoadError(f"loading {model_id} with backend {descriptor.backend!r} failed: {exc}") from exc
        self._loaded[model_id] = handle
        return self._registry.update_state(model_id, ModelState.LOADED, loaded_backend=descriptor.backend)

    def unload(self, model_id: str) -> ModelRecord:
        record = self._require(model_id)
        handle = self._loaded.pop(model_id, None)
        if handle is not None:
            handle.unload()
        if record.state is ModelState.LOADED:
            return self._registry.update_state(model_id, ModelState.READY, loaded_backend=None)
        return record

    def remove(self, model_id: str) -> None:
        record = self._require(model_id)
        if model_id in self._loaded or record.state is ModelState.LOADED:
            self.unload(model_id)
        self._storage.remove(record.kind, model_id)
        self._registry.remove(model_id)

    def is_loaded(self, model_id: str) -> bool:
        return model_id in self._loaded

    def loaded_handle(self, model_id: str) -> LoadedModel | None:
        return self._loaded.get(model_id)

    # -- backend availability ----------------------------------------------------

    def backend_availability(self) -> tuple[BackendAvailability, ...]:
        """One availability probe per registered backend.

        "http" is stdlib and always available. The lazy reference loaders
        probe `importlib.util.find_spec` for their runtime package.
        Custom-registered backends report available with a note that there
        is nothing to probe -- only their loader is known.
        """

        availability = []
        for name in self._backends.registered():
            if name == "http":
                availability.append(
                    BackendAvailability(name, True, "stdlib backend; always available")
                )
            elif name in _LAZY_RUNTIME_PACKAGES:
                package = _LAZY_RUNTIME_PACKAGES[name]
                try:
                    found = importlib.util.find_spec(package) is not None
                except (ImportError, ValueError):
                    found = False
                if found:
                    detail = f"runtime package '{package}' is importable"
                else:
                    detail = f"package not importable: install {package} to enable"
                availability.append(BackendAvailability(name, found, detail))
            else:
                availability.append(BackendAvailability(name, True, "custom loader registered"))
        return tuple(availability)

    # -- role assignments ----------------------------------------------------------

    def assign(self, role: str, model_id: str | None) -> None:
        """Persist which model serves a role; None clears the assignment.

        The role must be one of ROLES, and a model being assigned must
        already be registered and satisfy the role's kind + capability
        requirement (`ROLE_REQUIREMENTS`) -- a role is a routing promise,
        never a guess.
        """

        if not isinstance(role, str) or role not in ROLE_REQUIREMENTS:
            raise ModelManagerError(f"unknown role: {role!r}")
        if model_id is None:
            self._assignments.pop(role, None)
            self._save_assignments()
            return
        record = self._registry.get(model_id)
        if record is None:
            raise ModelNotFoundError(f"unknown model: {model_id}")
        kind, required = ROLE_REQUIREMENTS[role]
        if record.kind is not kind or not record.descriptor.supports_all(required):
            raise ModelManagerError(
                f"model {model_id!r} cannot serve role {role!r}: requires "
                f"kind={kind.value} capabilities={sorted(required)}, has "
                f"kind={record.kind.value} capabilities={sorted(record.descriptor.capabilities)}"
            )
        self._assignments[role] = model_id
        self._save_assignments()

    def assigned(self, role: str) -> str | None:
        if not isinstance(role, str) or role not in ROLE_REQUIREMENTS:
            raise ModelManagerError(f"unknown role: {role!r}")
        return self._assignments.get(role)

    def assignments(self) -> dict[str, str | None]:
        """Every role keyed, assigned model id or None -- a total mapping."""

        return {role: self._assignments.get(role) for role in ROLES}

    # -- internals ---------------------------------------------------------------

    def _require(self, model_id: str) -> ModelRecord:
        record = self._registry.get(model_id)
        if record is None:
            raise ModelNotFoundError(f"unknown model: {model_id}")
        return record

    def _register(self, manifest: ModelManifest, source: ModelSource, detail: str) -> ModelRecord:
        descriptor = manifest.to_descriptor(source, None, verified=False)
        return self._register_descriptor(descriptor, source, detail)

    def _register_descriptor(
        self, descriptor: ModelDescriptor, source: ModelSource, detail: str
    ) -> ModelRecord:
        record = ModelRecord(
            id=descriptor.id,
            kind=descriptor.kind,
            state=ModelState.REGISTERED,
            source_type=source,
            source_detail=detail,
            registered_at=datetime.now(timezone.utc),
            descriptor=descriptor,
        )
        return self._registry.add(record)

    def _verify_and_ready(self, manifest: ModelManifest, descriptor: ModelDescriptor) -> ModelRecord:
        if descriptor.source is not ModelSource.ENDPOINT:
            folder = self._storage.model_dir(manifest.kind, manifest.id)
            missing, mismatched = verify_files(_basename_keyed(manifest.sha256), folder)
            if missing or mismatched:
                self._registry.update_state(manifest.id, ModelState.HASH_MISMATCH)
                raise HashMismatchError(mismatched, missing)
        # Verification means the declared hashes matched; a hashless
        # (synthesized) manifest has nothing to verify against.
        verified = replace(descriptor, verified=bool(manifest.sha256))
        record = self._registry.get(manifest.id)
        self._registry.update_record(replace(record, descriptor=verified))
        self._registry.update_state(manifest.id, ModelState.VERIFIED)
        return self._registry.update_state(manifest.id, ModelState.READY)


__all__ = [
    "BackendAvailability",
    "BackendMissingError",
    "ModelLoadError",
    "ModelManager",
    "ModelManagerError",
    "ModelNotFoundError",
    "ROLE_ASR",
    "ROLE_CHAT",
    "ROLE_REQUIREMENTS",
    "ROLE_TTS",
    "ROLE_VAD",
    "ROLE_VISION",
    "ROLE_WAKE_WORD",
    "ROLES",
]
