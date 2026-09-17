"""Inspect-before-install over stub HTTP: fetch-and-parse only, streamed
downloads, hash verification, partial-destination cleanup, and Hugging Face
repo URL resolution (raw manifest first, API-synthesized fallback).
"""

import json
import tempfile
from pathlib import Path

import pytest

from haven.models import (
    HashMismatchError,
    ModelSourceError,
    download_files,
    inspect_manifest_url,
    resolve_model_url,
)
from haven.models.manifest import ModelManifest

from models_stub_http import StubServer, make_file_server


def test_inspect_fetches_and_parses_without_downloading_weights():
    server, url, manifest_dict, sha = make_file_server("inspect-me")
    try:
        inspection = inspect_manifest_url(url)
        assert inspection.manifest.id == "inspect-me"
        assert inspection.file_count == 2
        assert inspection.backend == "fake"
        assert inspection.license == "Apache-2.0"
        assert inspection.languages == frozenset({"en"})
        assert inspection.hardware == frozenset({"cpu"})
        assert inspection.hash_verification is True
        assert inspection.remote_code == "none"
        assert inspection.declared_bytes is None
    finally:
        server.shutdown()


def test_inspect_bad_json_is_a_source_error():
    server = StubServer({"/bad/haven-model.json": b"{ not json"})
    try:
        with pytest.raises(ModelSourceError, match="JSON"):
            inspect_manifest_url(server.url("/bad/haven-model.json"))
    finally:
        server.shutdown()


def test_inspect_invalid_manifest_is_a_source_error():
    server = StubServer({"/bad/haven-model.json": b'{"schema_version": "nope-2"}'})
    try:
        with pytest.raises(ModelSourceError, match="invalid"):
            inspect_manifest_url(server.url("/bad/haven-model.json"))
    finally:
        server.shutdown()


def test_inspect_unreachable_server_is_a_source_error():
    server, url, _, _ = make_file_server("gone")
    server.shutdown()
    with pytest.raises(ModelSourceError, match="cannot fetch"):
        inspect_manifest_url(url)


def test_download_verifies_and_reports_progress():
    server, url, manifest_dict, sha = make_file_server("dl-ok")
    try:
        manifest = ModelManifest.from_dict(manifest_dict)
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = download_files(
                manifest,
                base_url=url.rsplit("/", 1)[0] + "/",
                dest_dir=Path(tmp) / "out",
                progress=lambda received, total, name: seen.append((received, total, name)),
            )
            assert (dest / "weights.bin").read_bytes() == b"stub weights bytes"
            assert (dest / "config.json").is_file()
        assert seen and all(name in {"weights.bin", "config.json"} for _, _, name in seen)
        # per-file contract: (0, total, name) at start, (size, size, name) at end
        starts = [entry for entry in seen if entry[0] == 0]
        finals = [entry for entry in seen if entry[0] != 0 and entry[0] == entry[1]]
        assert {name for _, _, name in starts} == {"weights.bin", "config.json"}
        assert all(total == len(b"stub weights bytes") or total == len(b'{"layers": 1}')
                   for _, total, _ in starts)
        assert {name for _, _, name in finals} == {"weights.bin", "config.json"}
    finally:
        server.shutdown()


def test_download_hash_mismatch_cleans_up_partial_dest():
    server, url, manifest_dict, sha = make_file_server(
        "dl-bad", sha256_override={"weights.bin": "0" * 64}
    )
    try:
        manifest = ModelManifest.from_dict(manifest_dict)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            with pytest.raises(HashMismatchError) as excinfo:
                download_files(
                    manifest,
                    base_url=url.rsplit("/", 1)[0] + "/",
                    dest_dir=dest,
                )
            assert "weights.bin" in str(excinfo.value)
            assert not dest.exists()  # created-by-us destination removed
    finally:
        server.shutdown()


def test_download_missing_file_on_server_cleans_up():
    server, url, manifest_dict, sha = make_file_server("dl-missing")
    from urllib.parse import urlparse

    server._server.files.pop(urlparse(url).path.rsplit("/", 1)[0] + "/config.json")
    try:
        manifest = ModelManifest.from_dict(manifest_dict)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            with pytest.raises(ModelSourceError):
                download_files(manifest, base_url=url.rsplit("/", 1)[0] + "/", dest_dir=dest)
            assert not dest.exists()
    finally:
        server.shutdown()


def test_resolve_huggingface_repo_url_with_manifest_at_raw_main():
    server, _, manifest_dict, _ = make_file_server("hf-model", subdir="/openai/gguf-1/raw/main")
    try:
        repo_url = server.url("/openai/gguf-1")
        resolved = resolve_model_url(repo_url)
        assert resolved.manifest.id == "hf-model"
        assert resolved.base_url == server.url("/openai/gguf-1/resolve/main/")
        assert resolved.via == "huggingface"
        assert resolved.resolved_from == repo_url
        inspection = inspect_manifest_url(repo_url)
        assert inspection.manifest.id == "hf-model"
        assert inspection.resolved_from == repo_url
        assert inspection.remote_code == "none"
    finally:
        server.shutdown()


def test_resolve_huggingface_models_path_shape():
    # the /models/{org}/{repo} website route maps to the plain git path
    server, _, manifest_dict, _ = make_file_server("hf-model-2", subdir="/meta/llama/raw/main")
    try:
        resolved = resolve_model_url(server.url("/models/meta/llama"))
        assert resolved.manifest.id == "hf-model-2"
        assert resolved.base_url == server.url("/meta/llama/resolve/main/")
    finally:
        server.shutdown()


def test_resolve_huggingface_tree_path_shape():
    server, _, manifest_dict, _ = make_file_server("hf-model-3", subdir="/qwen/model/raw/main")
    try:
        resolved = resolve_model_url(server.url("/qwen/model/tree/main/sub/folder"))
        assert resolved.manifest.id == "hf-model-3"
    finally:
        server.shutdown()


def test_resolve_hf_repo_synthesizes_from_api_listing_when_no_manifest():
    files = {
        "/api/models/openai/gguf-1": json.dumps(
            {"siblings": [{"rfilename": "README.md"}, {"rfilename": "model.Q4_K_M.gguf"}]}
        ).encode("utf-8"),
        "/openai/gguf-1/resolve/main/model.Q4_K_M.gguf": b"gguf bytes",
    }
    server = StubServer(files)
    try:
        repo_url = server.url("/openai/gguf-1")
        resolved = resolve_model_url(repo_url)
        manifest = resolved.manifest
        assert manifest.id == "gguf-1"
        assert manifest.backend == "llama_cpp"
        assert manifest.files == {"model": "model.Q4_K_M.gguf"}
        assert manifest.sha256 == {}
        assert resolved.base_url == server.url("/openai/gguf-1/resolve/main/")
        assert resolved.manifest_url is None
        assert resolved.resolved_from == repo_url
        # inspect reports the synthesized manifest without downloading weights
        inspection = inspect_manifest_url(repo_url)
        assert inspection.manifest.backend == "llama_cpp"
        assert inspection.hash_verification is False
        assert inspection.resolved_from == repo_url
    finally:
        server.shutdown()


def test_resolve_unresolvable_url_names_accepted_shapes():
    server = StubServer({"/nowhere/": b"nope"})
    try:
        with pytest.raises(ModelSourceError, match="accepted shapes"):
            resolve_model_url(server.url("/nowhere/"))
    finally:
        server.shutdown()


def test_resolve_non_http_url_is_a_source_error():
    with pytest.raises(ModelSourceError, match="huggingface.co"):
        resolve_model_url("ftp://example/model")


def test_install_from_hf_repo_url_downloads_via_resolve_main():
    import tempfile as _tf
    from haven.models import ModelManager

    files = {
        "/api/models/acme/vision": json.dumps(
            {"siblings": [{"rfilename": "yolov8n.onnx"}]}
        ).encode("utf-8"),
        "/acme/vision/resolve/main/yolov8n.onnx": b"onnx bytes",
    }
    server = StubServer(files)
    try:
        with _tf.TemporaryDirectory() as tmp:
            manager = ModelManager(Path(tmp) / "root")
            record = manager.install_from_url(server.url("/acme/vision"))
            assert record.state.value == "ready"
            assert record.descriptor.backend == "onnx"
            assert (manager.models_root / "vision" / "vision" / "yolov8n.onnx").is_file()
    finally:
        server.shutdown()
