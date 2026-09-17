"""Layout detection: known folder shapes synthesize manifests, junk folders
are UNSUPPORTED naming what was looked for, and detection never writes.
"""

import tempfile
from pathlib import Path

import pytest

from haven.models import (
    ManifestError,
    ModelKind,
    ModelManifest,
    ModelState,
    detect_folder,
    manifest_filename,
    synthesize_from_files,
)


def _folder(root: Path, name: str, files: dict[str, bytes]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for filename, data in files.items():
        (folder / filename).write_bytes(data)
    return folder


def test_single_gguf_synthesizes_an_intelligence_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "llama-3.2", {"model.gguf": b"bytes"})
        detection = detect_folder(folder)
        assert detection.manifest is not None
        manifest = detection.manifest
        assert manifest.id == "llama-3-2"
        assert manifest.version == "0.0.0"
        assert manifest.kind is ModelKind.INTELLIGENCE
        assert manifest.capabilities == frozenset({"chat"})
        assert manifest.backend == "llama_cpp"
        assert manifest.architecture == "gguf"
        assert manifest.files == {"model": "model.gguf"}
        assert manifest.sha256 == {}
        assert manifest.license is None
        assert manifest.source == "detected:gguf"
        assert detection.state is ModelState.INSPECTED
        assert detection.confidence == "heuristic"
        assert any("license" in problem for problem in detection.problems)


def test_hf_safetensors_folder_reads_architecture_from_config():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(
            Path(tmp),
            "Qwen2.5-0.6B",
            {
                "config.json": b'{"architectures": ["Qwen2ForCausalLM"], "hidden_size": 1024}',
                "model.safetensors": b"small",
                "model-00002-of-00002.safetensors": b"larger weights here",
                "tokenizer.json": b"{}",
            },
        )
        manifest = detect_folder(folder).manifest
        assert manifest.backend == "transformers"
        assert manifest.architecture == "Qwen2ForCausalLM"
        # the largest weights file wins
        assert manifest.files == {"model": "model-00002-of-00002.safetensors", "config": "config.json"}


def test_hf_folder_with_junk_config_tolerates_it():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(
            Path(tmp),
            "mystery-bert",
            {"config.json": b"{ not json", "model.bin": b"weights"},
        )
        manifest = detect_folder(folder).manifest
        assert manifest.backend == "transformers"
        assert manifest.architecture == "unknown"


def test_safetensors_without_config_is_not_transformers_layout():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "loose-weights", {"model.safetensors": b"x"})
        detection = detect_folder(folder)
        assert detection.manifest is None
        assert detection.state is ModelState.UNSUPPORTED


@pytest.mark.parametrize(
    "filename, expected_kind, expected_capability",
    [
        ("yolov8n.onnx", ModelKind.VISION, "object_detection"),
        ("whisper-tiny.onnx", ModelKind.SPEECH, "asr"),
        ("piper-voice.onnx", ModelKind.SPEECH, "tts"),
        ("generic-encoder.onnx", ModelKind.SPECIALIZED, "inference"),
    ],
)
def test_onnx_layout_heuristics(filename, expected_kind, expected_capability):
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "onnx-model", {filename: b"onnx bytes"})
        manifest = detect_folder(folder).manifest
        assert manifest.kind is expected_kind
        assert manifest.capabilities == frozenset({expected_capability})
        assert manifest.backend == "onnx"
        assert manifest.architecture == "onnx"
        assert manifest.files == {"model": filename}


def test_onnx_json_pair_signals_piper_even_without_name_hint():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(
            Path(tmp),
            "en-us-voice",
            {"voice.onnx": b"onnx", "voice.onnx.json": b"{}"},
        )
        manifest = detect_folder(folder).manifest
        assert manifest.kind is ModelKind.SPEECH
        assert manifest.capabilities == frozenset({"tts"})


def test_junk_folder_is_unsupported_naming_what_was_looked_for():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "downloads", {"readme.txt": b"hi", "data.csv": b"1,2"})
        detection = detect_folder(folder)
        assert detection.manifest is None
        assert detection.state is ModelState.UNSUPPORTED
        problem = " ".join(detection.problems)
        assert "*.gguf" in problem and "*.onnx" in problem and "config.json" in problem
        assert detection.confidence == "none"


def test_folder_with_real_manifest_stays_a_declared_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "declared", {"weights.bin": b"w"})
        manifest = ModelManifest.from_dict(
            {
                "schema_version": "haven-model-1",
                "id": "declared",
                "version": "1.0.0",
                "kind": "intelligence",
                "capabilities": ["chat"],
                "architecture": "stub",
                "backend": "fake",
                "files": {"weights": "weights.bin"},
                "sha256": {"weights.bin": "0" * 64},
                "license": "MIT",
            }
        )
        manifest.save(folder / manifest_filename())
        detection = detect_folder(folder)
        assert detection.manifest == manifest
        assert detection.state is ModelState.INSPECTED
        assert detection.confidence == "high"
        assert detection.problems == ()


def test_broken_manifest_is_unsupported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "broken", {manifest_filename(): b"{ nope"})
        detection = detect_folder(folder)
        assert detection.manifest is None
        assert detection.state is ModelState.UNSUPPORTED
        assert any("JSON" in problem for problem in detection.problems)


def test_unusable_folder_name_is_unsupported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "!!!", {"model.gguf": b"bytes"})
        detection = detect_folder(folder)
        assert detection.manifest is None
        assert detection.state is ModelState.UNSUPPORTED
        assert any("model id" in problem for problem in detection.problems)


def test_synthesize_from_files_returns_none_for_unknown_layouts():
    assert synthesize_from_files(["a.txt", "b.md"], name_hint="docs") is None
    # multiple ggufs are not "a single *.gguf" — refuse to guess
    assert synthesize_from_files(["a.gguf", "b.gguf"], name_hint="two") is None


def test_synthesize_from_files_slugifies_the_name_hint():
    manifest = synthesize_from_files(["model.gguf"], name_hint="Org/Repo Name")
    assert manifest.id == "org-repo-name"
    with pytest.raises(Exception):
        synthesize_from_files(["model.gguf"], name_hint="!!!")


def test_detection_never_writes():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _folder(Path(tmp), "gguf", {"model.gguf": b"x"})
        before = sorted(p.name for p in folder.iterdir())
        detect_folder(folder)
        after = sorted(p.name for p in folder.iterdir())
        assert before == after
        assert not (folder / manifest_filename()).exists()


def test_manifest_rejects_absolute_and_driven_file_paths():
    for bad in ("/abs/weights.bin", "C:\\weights.bin", "../weights.bin", "sub/weights.bin"):
        with pytest.raises(ManifestError):
            ModelManifest.from_dict(
                {
                    "schema_version": "haven-model-1",
                    "id": "m",
                    "version": "1.0.0",
                    "kind": "intelligence",
                    "capabilities": ["chat"],
                    "architecture": "a",
                    "backend": "b",
                    "files": {"weights": bad},
                    "sha256": {},
                }
            )
