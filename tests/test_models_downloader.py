"""Inspect-before-install over stub HTTP: fetch-and-parse only, streamed
downloads, hash verification, and partial-destination cleanup.
"""

import tempfile
from pathlib import Path

import pytest

from haven.models import HashMismatchError, ModelSourceError, download_files, inspect_manifest_url
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
                progress=lambda nbytes, name: seen.append((nbytes, name)),
            )
            assert (dest / "weights.bin").read_bytes() == b"stub weights bytes"
            assert (dest / "config.json").is_file()
        assert seen and all(name in {"weights.bin", "config.json"} for _, name in seen)
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
