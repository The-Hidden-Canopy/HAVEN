"""The universal haven-model.json schema: round-trip, mapping, and every
validation rule including first-class hash coverage and the closed kind
taxonomy.
"""

import json

import pytest

from haven.models import (
    SCHEMA_VERSION,
    ManifestError,
    ModelKind,
    ModelManifest,
    ModelSource,
    manifest_filename,
)

BASE_MANIFEST = {
    "schema_version": "haven-model-1",
    "id": "example-model",
    "version": "1.0.0",
    "kind": "intelligence",
    "capabilities": ["chat", "structured_intent"],
    "architecture": "qwen",
    "backend": "transformers",
    "files": {"config": "config.json", "weights": "model.safetensors", "tokenizer": "tokenizer.json"},
    "sha256": {
        "config.json": "a" * 64,
        "model.safetensors": "b" * 64,
        "tokenizer.json": "c" * 64,
    },
    "languages": ["en"],
    "hardware": {"cpu": True, "cuda": True, "rocm": False},
    "license": "Apache-2.0",
    "source": "https://example.com/models/example-model/",
}


def _manifest(**overrides) -> ModelManifest:
    data = dict(BASE_MANIFEST)
    data.update(overrides)
    return ModelManifest.from_dict(data)


def test_round_trip_preserves_everything():
    manifest = _manifest()
    rebuilt = ModelManifest.from_dict(manifest.to_dict())
    assert rebuilt == manifest
    assert manifest.kind is ModelKind.INTELLIGENCE
    assert manifest.device_support == frozenset({"cpu", "cuda"})
    assert manifest.languages == frozenset({"en"})


def test_load_and_save_round_trip_via_disk():
    import tempfile
    from pathlib import Path

    manifest = _manifest()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / manifest_filename()
        manifest.save(path)
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION
        assert ModelManifest.load(path) == manifest


def test_missing_file_cannot_load():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ManifestError, match="not found"):
            ModelManifest.load(Path(tmp) / manifest_filename())


def test_bad_schema_version_is_rejected():
    with pytest.raises(ManifestError, match="schema_version"):
        _manifest(schema_version="other-2")


def test_unknown_kind_names_the_kind():
    with pytest.raises(ManifestError, match="robotics"):
        _manifest(kind="robotics")


def test_declared_file_without_hash_is_an_error():
    sha = dict(BASE_MANIFEST["sha256"])
    del sha["tokenizer.json"]
    with pytest.raises(ManifestError, match="tokenizer.json"):
        _manifest(sha256=sha)


def test_empty_files_map_is_rejected():
    with pytest.raises(ManifestError, match="files"):
        _manifest(files={}, sha256={})


def test_empty_capabilities_are_rejected():
    with pytest.raises(ManifestError, match="capabilities"):
        _manifest(capabilities=[])


def test_required_text_fields_are_validated():
    for field in ("id", "version", "architecture", "backend"):
        with pytest.raises(ManifestError):
            _manifest(**{field: ""})


def test_hardware_maps_to_device_support_open_vocabulary():
    manifest = _manifest(hardware={"cpu": True, "mps": True, "cuda": False})
    assert manifest.device_support == frozenset({"cpu", "mps"})


def test_languages_default_to_empty_set():
    data = dict(BASE_MANIFEST)
    del data["languages"]
    manifest = ModelManifest.from_dict(data)
    assert manifest.languages == frozenset()


def test_manifest_filename_is_stable():
    assert manifest_filename() == "haven-model.json"


def test_to_descriptor_maps_onto_the_contract():
    manifest = _manifest()
    descriptor = manifest.to_descriptor(ModelSource.DOWNLOADED, "/root/intelligence/example-model")
    assert descriptor.id == "example-model"
    assert descriptor.kind is ModelKind.INTELLIGENCE
    assert descriptor.capabilities == frozenset({"chat", "structured_intent"})
    assert descriptor.backend == "transformers"
    assert descriptor.architecture == "qwen"
    assert descriptor.source is ModelSource.DOWNLOADED
    assert descriptor.path == "/root/intelligence/example-model"
    assert descriptor.languages == frozenset({"en"})
    assert descriptor.device_support == frozenset({"cpu", "cuda"})
    assert descriptor.license == "Apache-2.0"
    assert descriptor.files["weights"] == "model.safetensors"
    assert descriptor.sha256["config.json"] == "a" * 64
    assert descriptor.verified is False


def test_invalid_json_manifest_raises_typed_error():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / manifest_filename()
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ManifestError, match="JSON"):
            ModelManifest.load(path)


def test_model_id_is_validated_as_a_safe_storage_identifier():
    with pytest.raises(ManifestError, match="model id"):
        _manifest(id="../escape")
    with pytest.raises(ManifestError, match="model id"):
        _manifest(id="a/b")
    assert _manifest(id="my.model_2").id == "my.model_2"


@pytest.mark.parametrize(
    "bad_path",
    ["../weights.bin", "sub/weights.bin", "sub\\weights.bin", "/abs/weights.bin",
     "C:\\weights.bin", "C:weights.bin", "", "  ", "..", "weird..name.bin"],
)
def test_declared_file_paths_must_be_relative_basenames(bad_path):
    with pytest.raises(ManifestError) as excinfo:
        _manifest(files={"weights": bad_path}, sha256={})
    assert repr(bad_path.strip()) in str(excinfo.value) or bad_path.strip() in str(excinfo.value)


@pytest.mark.parametrize("bad_key", ["../weights.bin", "sub/weights.bin", "C:\\x.bin"])
def test_sha256_keys_must_be_relative_basenames(bad_key):
    with pytest.raises(ManifestError, match="basename"):
        _manifest(sha256={bad_key: "a" * 64})


def test_endpoint_field_is_carried_to_the_descriptor():
    data = dict(BASE_MANIFEST)
    data["endpoint"] = "https://api.example.com/v1/"
    data["files"] = {}
    data["sha256"] = {}
    manifest = ModelManifest.from_dict(data)
    assert manifest.endpoint == "https://api.example.com/v1/"
    rebuilt = ModelManifest.from_dict(manifest.to_dict())
    assert rebuilt.endpoint == "https://api.example.com/v1/"
    descriptor = manifest.to_descriptor(ModelSource.ENDPOINT, None)
    assert descriptor.endpoint_url == "https://api.example.com/v1/"


def test_endpoint_field_must_be_an_http_url():
    with pytest.raises(ManifestError, match="endpoint"):
        _manifest(endpoint="ftp://example")


def test_a_hashless_manifest_is_legal_but_partial_coverage_is_not():
    hashless = _manifest(sha256={})
    assert hashless.sha256 == {}
    partial = dict(BASE_MANIFEST)
    del partial["sha256"]["tokenizer.json"]
    with pytest.raises(ManifestError, match="tokenizer.json"):
        ModelManifest.from_dict(partial)
