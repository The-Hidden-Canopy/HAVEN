"""Models-root layout: flat-by-basename installs, replace semantics,
endpoint installs, and the default root under ~/.haven/models.
"""

import tempfile
from pathlib import Path

import pytest

from haven.models import ModelStorage, StorageError, default_models_root
from haven.models.manifest import ModelManifest, manifest_filename

from models_stub_http import make_manifest_dict


def _write_model_folder(parent: Path, model_id: str = "local-model") -> Path:
    manifest = ModelManifest.from_dict(make_manifest_dict(model_id))
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"local weights")
    manifest.save(folder / manifest_filename())
    return folder


def _storage(tmp: str) -> ModelStorage:
    return ModelStorage(Path(tmp) / "models")


def test_install_copies_files_flat_plus_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        source = _write_model_folder(Path(tmp))
        storage = _storage(tmp)
        manifest = ModelManifest.load(source / manifest_filename())
        dest = storage.install(manifest, source)
        assert dest == storage.model_dir(manifest.kind, manifest.id)
        assert (dest / "weights.bin").read_bytes() == b"local weights"
        assert (dest / manifest_filename()).is_file()
        assert storage.is_installed(manifest.kind, manifest.id)


def test_install_refuses_unless_replace():
    with tempfile.TemporaryDirectory() as tmp:
        source = _write_model_folder(Path(tmp))
        storage = _storage(tmp)
        manifest = ModelManifest.load(source / manifest_filename())
        storage.install(manifest, source)
        with pytest.raises(StorageError, match="already installed"):
            storage.install(manifest, source)
        (source / "weights.bin").write_bytes(b"replaced weights")
        storage.install(manifest, source, replace=True)
        assert (storage.model_dir(manifest.kind, manifest.id) / "weights.bin").read_bytes() == b"replaced weights"


def test_install_fails_when_a_declared_file_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        source = _write_model_folder(Path(tmp))
        (source / "weights.bin").unlink()
        storage = _storage(tmp)
        manifest = ModelManifest.load(source / manifest_filename())
        with pytest.raises(StorageError, match="weights"):
            storage.install(manifest, source)
        assert not storage.is_installed(manifest.kind, manifest.id)


def test_install_flattens_nested_declared_paths_by_basename():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "nested"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "weights.bin").write_bytes(b"nested weights")
        manifest = ModelManifest.from_dict(
            {
                **make_manifest_dict(),
                "files": {"weights": "sub/weights.bin"},
                "sha256": {"sub/weights.bin": __import__("hashlib").sha256(b"nested weights").hexdigest()},
            }
        )
        storage = _storage(tmp)
        dest = storage.install(manifest, source)
        assert (dest / "weights.bin").read_bytes() == b"nested weights"


def test_install_endpoint_writes_manifest_only():
    with tempfile.TemporaryDirectory() as tmp:
        source = _write_model_folder(Path(tmp))
        storage = _storage(tmp)
        manifest = ModelManifest.load(source / manifest_filename())
        dest = storage.install_endpoint(manifest)
        assert (dest / manifest_filename()).is_file()
        assert not (dest / "weights.bin").exists()
        with pytest.raises(StorageError, match="already installed"):
            storage.install_endpoint(manifest)
        storage.install_endpoint(manifest, replace=True)
        assert dest.is_dir()


def test_remove_and_is_installed():
    with tempfile.TemporaryDirectory() as tmp:
        source = _write_model_folder(Path(tmp))
        storage = _storage(tmp)
        manifest = ModelManifest.load(source / manifest_filename())
        assert storage.remove(manifest.kind, manifest.id) is False
        storage.install(manifest, source)
        assert storage.is_installed(manifest.kind, manifest.id)
        assert storage.remove(manifest.kind, manifest.id) is True
        assert not storage.is_installed(manifest.kind, manifest.id)


def test_default_models_root_is_under_home():
    root = default_models_root()
    assert root.name == "models"
    assert root.parent.name == ".haven"
