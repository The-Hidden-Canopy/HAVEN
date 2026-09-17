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

import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .backends import BackendRegistry, LoadedModel
from .catalog import CatalogEntry
from .contracts import ModelDescriptor, ModelKind, ModelSource
from .discovery import DiscoveryResult, scan_roots
from .downloader import (
    HashMismatchError,
    ModelSourceError,
    UrlInspection,
    download_files,
    inspect_manifest_url,
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
_PROBE_TIMEOUT_SECONDS = 5


def _base_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def _basename_keyed(sha256: dict[str, str]) -> dict[str, str]:
    return {Path(rel).name: digest for rel, digest in sha256.items()}


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
        self._backends = backends if backends is not None else BackendRegistry()
        self._catalog = tuple(catalog)
        self._loaded: dict[str, LoadedModel] = {}
        self._roots_path = self.models_root / _ROOTS_FILENAME
        self._roots: list[str] = self._load_roots()

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
        """Download entry path: inspect -> download -> verify -> READY."""

        inspection = inspect_manifest_url(url)
        manifest = inspection.manifest
        self._register(manifest, ModelSource.DOWNLOADED, url)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                download_files(manifest, base_url=_base_url(url), dest_dir=Path(tmp))
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
        """Local folder entry path: manifest load -> install -> verify."""

        folder = Path(folder)
        manifest = ModelManifest.load(folder / "haven-model.json")
        self._register(manifest, ModelSource.LOCAL, str(folder))
        dest = self._storage.install(manifest, folder, replace=replace)
        descriptor = manifest.to_descriptor(ModelSource.LOCAL, str(dest), verified=False)
        return self._verify_and_ready(manifest, descriptor)

    # -- entry path: Register External -----------------------------------------

    def register_endpoint(self, manifest_or_url) -> ModelRecord:
        """Endpoint entry path: descriptor over a base URL, no files.

        A lightweight GET failure at registration time is recorded as
        UNREACHABLE but does not block registration: endpoints flap, and
        the record is the flag.
        """

        if isinstance(manifest_or_url, str):
            url = manifest_or_url
            manifest = inspect_manifest_url(url).manifest
            base = _base_url(url)
        elif isinstance(manifest_or_url, ModelManifest):
            manifest = manifest_or_url
            if not manifest.source:
                raise ModelManagerError("endpoint manifest must declare a source URL")
            base = _base_url(manifest.source)
        else:
            raise ModelManagerError("register_endpoint expects a manifest or a manifest URL")
        descriptor = ModelDescriptor(
            id=manifest.id,
            kind=manifest.kind,
            capabilities=manifest.capabilities,
            backend=manifest.backend,
            architecture=manifest.architecture,
            version=manifest.version,
            source=ModelSource.ENDPOINT,
            path=base,
            languages=manifest.languages,
            device_support=manifest.device_support,
            license=manifest.license,
            verified=False,
            files={},
            sha256={},
        )
        record = self._register_descriptor(descriptor, ModelSource.ENDPOINT, base)
        self._storage.install_endpoint(manifest, replace=True)
        if not self._probe(base):
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
        verified = replace(descriptor, verified=True)
        record = self._registry.get(manifest.id)
        self._registry.update_record(replace(record, descriptor=verified))
        self._registry.update_state(manifest.id, ModelState.VERIFIED)
        return self._registry.update_state(manifest.id, ModelState.READY)


__all__ = [
    "BackendMissingError",
    "ModelLoadError",
    "ModelManager",
    "ModelManagerError",
    "ModelNotFoundError",
]
