"""ModelManager: the three entry paths, capability-routing resolve, the
simultaneous-load topology, explicit failure states, and discovery gating.
"""

import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.models import (
    BackendMissingError,
    BackendRegistry,
    CatalogEntry,
    ModelKind,
    ModelLoadError,
    ModelManager,
    ModelManagerError,
    ModelNotFoundError,
    ModelSource,
    ModelState,
    inspect_folder,
)
from haven.models.manifest import ModelManifest, manifest_filename
from haven.models.storage import StorageError

from models_stub_http import make_file_server, make_manifest_dict


class FakeLoadedModel:
    def __init__(self, descriptor):
        self._descriptor = descriptor
        self.unloaded = False

    @property
    def descriptor(self):
        return self._descriptor

    def unload(self):
        self.unloaded = True


class FakeBackend:
    """The loader plugin tests register; HAVEN Core ships none."""

    def __init__(self):
        self.calls = []
        self.fail_ids = set()

    def load(self, descriptor, model_dir):
        if descriptor.id in self.fail_ids:
            raise RuntimeError("simulated loader failure")
        self.calls.append((descriptor.id, model_dir))
        return FakeLoadedModel(descriptor)


class _FakeDateTime:
    """Deterministic registered_at so recency assertions are exact."""

    def __init__(self):
        self._tick = 0

    def now(self, tz=None):
        self._tick += 1
        return datetime(2026, 9, 16, tzinfo=timezone.utc) + timedelta(seconds=self._tick)


@pytest.fixture()
def clock(monkeypatch):
    fake = _FakeDateTime()
    monkeypatch.setattr("haven.models.manager.datetime", fake)
    return fake


@pytest.fixture()
def backend():
    return FakeBackend()


@pytest.fixture()
def manager_factory(backend):
    registry = BackendRegistry()
    registry.register("fake", backend)
    managers = []

    def make(root, **kwargs):
        kwargs.setdefault("backends", registry)
        manager = ModelManager(root, **kwargs)
        managers.append(manager)
        return manager

    return make


def _write_local_model(parent: Path, model_id: str, **manifest_kwargs) -> Path:
    manifest = ModelManifest.from_dict(make_manifest_dict(model_id, **manifest_kwargs))
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"local weights")
    manifest.save(folder / manifest_filename())
    return folder


def test_install_from_url_end_to_end(manager_factory):
    server, url, _, _ = make_file_server("url-model")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = manager_factory(Path(tmp) / "root")
            record = manager.install_from_url(url)
            assert record.state is ModelState.READY
            assert record.source_type is ModelSource.DOWNLOADED
            assert record.source_detail == url
            assert record.descriptor.verified is True
            installed = manager.models_root / "intelligence" / "url-model"
            assert (installed / "weights.bin").read_bytes() == b"stub weights bytes"
            # state persisted: a fresh manager over the same root agrees
            assert ModelManager(Path(tmp) / "root").get("url-model").state is ModelState.READY
    finally:
        server.shutdown()


def test_install_local_folder(manager_factory):
    with tempfile.TemporaryDirectory() as tmp:
        folder = _write_local_model(Path(tmp), "local-model", languages=("en", "fr"))
        manager = manager_factory(Path(tmp) / "root")
        record = manager.install_local_folder(folder)
        assert record.state is ModelState.READY
        assert record.source_type is ModelSource.LOCAL
        assert record.descriptor.path.endswith(str(Path("intelligence") / "local-model"))
        assert record.descriptor.languages == frozenset({"en", "fr"})


def test_install_refuses_double_install_without_replace(manager_factory):
    with tempfile.TemporaryDirectory() as tmp:
        folder = _write_local_model(Path(tmp), "local-model")
        manager = manager_factory(Path(tmp) / "root")
        manager.install_local_folder(folder)
        with pytest.raises(StorageError):
            manager.install_local_folder(folder)
        record = manager.install_local_folder(folder, replace=True)
        assert record.state is ModelState.READY


def test_search_filters_the_catalog():
    entries = (
        CatalogEntry(
            id="wake-tiny", kind=ModelKind.SPEECH,
            manifest_url="https://x.example/wake/haven-model.json",
            description="Tiny wake-word model",
        ),
        CatalogEntry(
            id="agent-pro", kind=ModelKind.INTELLIGENCE,
            manifest_url="https://x.example/agent/haven-model.json",
            description="Licensed household agent",
        ),
    )
    manager = ModelManager(None, catalog=entries)
    assert {e.id for e in manager.search()} == {"wake-tiny", "agent-pro"}
    assert {e.id for e in manager.search("wake")} == {"wake-tiny"}
    assert manager.search("zzz") == ()


def test_inspect_url_passthrough(manager_factory):
    server, url, _, _ = make_file_server("inspect-me")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            inspection = manager_factory(Path(tmp) / "root").inspect_url(url)
            assert inspection.manifest.id == "inspect-me"
    finally:
        server.shutdown()


def test_resolve_by_kind_picks_newest_ready(manager_factory, clock):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "older-model")
        _write_local_model(Path(tmp), "newer-model")
        manager.install_local_folder(Path(tmp) / "older-model")
        manager.install_local_folder(Path(tmp) / "newer-model")
        assert manager.resolve(ModelKind.INTELLIGENCE).id == "newer-model"


def test_resolve_matches_capabilities_and_languages(manager_factory):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "english-chat", capabilities=("chat",), languages=("en",))
        _write_local_model(Path(tmp), "multilingual", capabilities=("chat", "summarizer"), languages=("en", "es"))
        manager.install_local_folder(Path(tmp) / "english-chat")
        manager.install_local_folder(Path(tmp) / "multilingual")
        assert manager.resolve("intelligence", requires={"chat"}).id == "multilingual"
        assert manager.resolve("intelligence", requires={"summarizer"}).id == "multilingual"
        assert manager.resolve("intelligence", requires={"chat"}, languages={"es"}).id == "multilingual"
        assert manager.resolve("intelligence", languages={"en"}).id == "multilingual"
        with pytest.raises(ModelNotFoundError):
            manager.resolve("intelligence", requires={"planner"})
        with pytest.raises(ModelNotFoundError):
            manager.resolve("intelligence", languages={"de"})
        with pytest.raises(ModelNotFoundError):
            manager.resolve("vision")


def test_resolve_prefers_loaded_over_newer_ready(manager_factory, backend, clock):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "old-model")
        _write_local_model(Path(tmp), "new-model")
        manager.install_local_folder(Path(tmp) / "old-model")
        manager.install_local_folder(Path(tmp) / "new-model")
        manager.load("old-model")
        assert manager.resolve("intelligence").id == "old-model"
        assert manager.get("new-model").state is ModelState.READY


def test_resolve_never_matches_on_architecture_backend_or_id(manager_factory, clock):
    with tempfile.TemporaryDirectory() as tmp:
        registry = BackendRegistry()
        registry.register("backend-a", FakeBackend())
        registry.register("backend-b", FakeBackend())
        manager = manager_factory(Path(tmp) / "root", backends=registry)
        _write_local_model(Path(tmp), "arch-qwen", architecture="qwen", backend="backend-a")
        _write_local_model(Path(tmp), "arch-llama", architecture="llama", backend="backend-b")
        manager.install_local_folder(Path(tmp) / "arch-qwen")
        manager.install_local_folder(Path(tmp) / "arch-llama")
        # identical kind + capabilities -> recency decides, architecture/backend irrelevant
        assert manager.resolve("intelligence").id == "arch-llama"
        manager.load("arch-qwen")
        # LOADED wins over newer READY even though its architecture/backend differ
        assert manager.resolve("intelligence").id == "arch-qwen"


def test_load_with_registered_backend_and_unload(manager_factory, backend):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "loadable")
        manager.install_local_folder(Path(tmp) / "loadable")
        record = manager.load("loadable")
        assert record.state is ModelState.LOADED
        assert record.loaded_backend == "fake"
        assert manager.is_loaded("loadable")
        handle = manager.loaded_handle("loadable")
        assert handle.descriptor.id == "loadable"
        assert backend.calls[0][1].name == "loadable"  # loader got the model dir
        # unload: handle released, record back to READY, idempotent
        record = manager.unload("loadable")
        assert record.state is ModelState.READY
        assert record.loaded_backend is None
        assert handle.unloaded is True
        assert not manager.is_loaded("loadable")
        manager.unload("loadable")
        with pytest.raises(ModelNotFoundError):
            manager.unload("nope")


def test_multiple_models_loaded_simultaneously(manager_factory, backend):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "wake-word", kind="speech", capabilities=("wake_word",))
        _write_local_model(Path(tmp), "vad", kind="speech", capabilities=("vad",))
        _write_local_model(Path(tmp), "agent", kind="intelligence", capabilities=("chat",))
        for model_id in ("wake-word", "vad", "agent"):
            manager.install_local_folder(Path(tmp) / model_id)
        for model_id in ("wake-word", "vad", "agent"):
            manager.load(model_id)
        loaded = {r.id: r.state for r in manager.list_models()}
        assert loaded == {"wake-word": ModelState.LOADED, "vad": ModelState.LOADED, "agent": ModelState.LOADED}
        assert manager.resolve("speech", requires={"wake_word"}).id == "wake-word"
        assert manager.resolve("speech", requires={"vad"}).id == "vad"
        assert len(backend.calls) == 3


def test_load_without_backend_records_backend_missing(manager_factory, backend):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "nobackend", backend="unregistered-backend")
        manager.install_local_folder(Path(tmp) / "nobackend")
        with pytest.raises(BackendMissingError, match="unregistered-backend"):
            manager.load("nobackend")
        record = manager.get("nobackend")
        assert record.state is ModelState.BACKEND_MISSING
        assert ModelManager(Path(tmp) / "root").get("nobackend").state is ModelState.BACKEND_MISSING


def test_load_records_register_name_not_architecture(manager_factory, backend):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "boom", backend="fake")
        manager.install_local_folder(Path(tmp) / "boom")
        backend.fail_ids.add("boom")
        with pytest.raises(ModelLoadError, match="boom"):
            manager.load("boom")
        record = manager.get("boom")
        assert record.state is ModelState.LOAD_FAILED
        assert not manager.is_loaded("boom")


def test_remove_unloads_first_and_clears_everything(manager_factory, backend):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        manager = manager_factory(root)
        _write_local_model(Path(tmp), "doomed")
        manager.install_local_folder(Path(tmp) / "doomed")
        manager.load("doomed")
        handle = manager.loaded_handle("doomed")
        manager.remove("doomed")
        assert handle.unloaded is True
        assert manager.get("doomed") is None
        assert not (root / "intelligence" / "doomed").exists()
        with pytest.raises(ModelNotFoundError):
            manager.remove("doomed")


def test_register_endpoint_ready_when_reachable(manager_factory):
    server, url, manifest_dict, _ = make_file_server("ep-model")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = replace(ModelManifest.from_dict(manifest_dict), source=url)
            manager = manager_factory(Path(tmp) / "root")
            record = manager.register_endpoint(manifest)
            assert record.state is ModelState.READY
            assert record.source_type is ModelSource.ENDPOINT
            descriptor = record.descriptor
            assert descriptor.source is ModelSource.ENDPOINT
            assert descriptor.files == {}
            assert descriptor.path == url.rsplit("/", 1)[0] + "/"
            assert descriptor.verified is False
            # resolvable like any other ready model
            assert manager.resolve("intelligence").id == "ep-model"
    finally:
        server.shutdown()


def test_register_endpoint_unreachable_is_flagged_not_blocked(manager_factory):
    server, url, manifest_dict, _ = make_file_server("flappy")
    server.shutdown()  # the endpoint is down at registration time
    with tempfile.TemporaryDirectory() as tmp:
        manifest = replace(ModelManifest.from_dict(manifest_dict), source=url)
        manager = manager_factory(Path(tmp) / "root")
        record = manager.register_endpoint(manifest)
        assert record.state is ModelState.UNREACHABLE
        assert manager.get("flappy").state is ModelState.UNREACHABLE
        with pytest.raises(ModelNotFoundError):
            manager.resolve("intelligence")


def test_register_endpoint_from_url(manager_factory):
    server, url, _, _ = make_file_server("ep-by-url")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = manager_factory(Path(tmp) / "root")
            record = manager.register_endpoint(url)
            assert record.state is ModelState.READY
            assert record.descriptor.source is ModelSource.ENDPOINT
    finally:
        server.shutdown()


def test_add_root_scan_and_register_candidate(manager_factory):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "models-root"
        model_folder = _write_local_model(root, "scanned-model")
        manager = manager_factory(Path(tmp) / "root")
        assert manager.roots() == ()
        assert manager.scan() == []
        manager.add_root(root)
        assert manager.roots() == (str(root),)
        # roots survive reload
        assert ModelManager(Path(tmp) / "root").roots() == (str(root),)
        results = manager.scan()
        assert [(r.path.name, r.state) for r in results] == [("scanned-model", ModelState.INSPECTED)]
        # discovery alone never registers
        assert manager.get("scanned-model") is None
        record = manager.register_candidate(results[0])
        assert record.state is ModelState.READY
        assert record.source_type is ModelSource.LOCAL
        assert (Path(tmp) / "root" / "intelligence" / "scanned-model" / "weights.bin").is_file()


def test_register_candidate_without_manifest_is_refused(manager_factory):
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "mystery"
        folder.mkdir()
        manager = manager_factory(Path(tmp) / "root")
        with pytest.raises(ModelManagerError, match="manifest"):
            manager.register_candidate(inspect_folder(folder))


def test_hash_mismatch_during_url_install_is_recorded(manager_factory):
    server, url, manifest_dict, _ = make_file_server(
        "corrupt-model", sha256_override={"weights.bin": "f" * 64}
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = manager_factory(Path(tmp) / "root")
            with pytest.raises(Exception, match="weights.bin"):
                manager.install_from_url(url)
            assert manager.get("corrupt-model").state is ModelState.HASH_MISMATCH
    finally:
        server.shutdown()


def test_every_listing_and_lookup_shape(manager_factory):
    with tempfile.TemporaryDirectory() as tmp:
        manager = manager_factory(Path(tmp) / "root")
        _write_local_model(Path(tmp), "m1")
        _write_local_model(Path(tmp), "m2", kind="vision", capabilities=("object_detection",))
        manager.install_local_folder(Path(tmp) / "m1")
        manager.install_local_folder(Path(tmp) / "m2")
        assert {r.id for r in manager.list_models()} == {"m1", "m2"}
        assert {r.id for r in manager.list_models(ModelState.READY)} == {"m1", "m2"}
        assert manager.list_models(ModelState.LOADED) == ()
        assert manager.get("m1").kind is ModelKind.INTELLIGENCE
        assert manager.get("nope") is None
