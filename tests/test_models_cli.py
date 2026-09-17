"""`python -m haven.models` operator surface: one happy-path test per
subcommand plus exit-code discipline (0 ok, 1 runtime error, 2 usage).
"""

import json
import tempfile
from pathlib import Path

import pytest

from haven.models.__main__ import main

from models_stub_http import make_file_server, make_manifest_dict
from haven.models.manifest import ModelManifest, manifest_filename


def _write_local_model(parent: Path, model_id: str, **overrides) -> Path:
    manifest = ModelManifest.from_dict(make_manifest_dict(model_id, **overrides))
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"local weights")
    manifest.save(folder / manifest_filename())
    return folder


def test_search_with_catalog_file(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        catalog = Path(tmp) / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "wake-tiny",
                            "kind": "speech",
                            "manifest_url": "https://x.example/wake/haven-model.json",
                            "description": "Tiny wake-word model",
                            "license": "Apache-2.0",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert main(["search", "wake", "--catalog", str(catalog)]) == 0
        out = capsys.readouterr().out
        assert "wake-tiny" in out and "speech" in out and "Apache-2.0" in out
        assert main(["search", "nothing-matches", "--catalog", str(catalog)]) == 0
        assert capsys.readouterr().out == ""


def test_search_without_catalog_is_empty(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        assert main(["search", "--root", str(Path(tmp) / "root")]) == 0
        assert capsys.readouterr().out == ""


def test_inspect_subcommand(capsys):
    server, url, _, _ = make_file_server("inspect-me")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            assert main(["inspect", url, "--root", str(Path(tmp) / "root")]) == 0
        out = capsys.readouterr().out
        assert "id: inspect-me" in out
        assert "backend: fake" in out
        assert "hash verification: yes" in out
        assert "remote code: none" in out
    finally:
        server.shutdown()


def test_download_and_list_and_resolve_flow(capsys):
    server, url, _, _ = make_file_server("cli-model")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "root")
            assert main(["download", url, "--root", root]) == 0
            assert "installed cli-model (ready)" in capsys.readouterr().out
            assert main(["list", "--root", root]) == 0
            out = capsys.readouterr().out
            assert "cli-model | intelligence | ready | downloaded" in out
            assert main(["resolve", "intelligence", "--requires", "chat", "--root", root]) == 0
            assert capsys.readouterr().out.strip() == "cli-model"
    finally:
        server.shutdown()


def test_install_local_subcommand(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        _write_local_model(Path(tmp), "cli-local")
        root = str(Path(tmp) / "root")
        assert main(["install-local", str(Path(tmp) / "cli-local"), "--root", root]) == 0
        assert "installed cli-local (ready) [local]" in capsys.readouterr().out


def test_add_endpoint_and_load_unload_remove_lifecycle(capsys):
    server, url, _, _ = make_file_server("cli-ep")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "root")
            assert main(["add-endpoint", url, "--root", root]) == 0
            assert "registered cli-ep (ready) [endpoint]" in capsys.readouterr().out
            # no backend plugin registered -> load fails with BACKEND_MISSING
            assert main(["load", "cli-ep", "--root", root]) == 1
            assert "backend" in capsys.readouterr().err
            assert main(["list", "--state", "backend_missing", "--root", root]) == 0
            assert "cli-ep" in capsys.readouterr().out
            assert main(["unload", "cli-ep", "--root", root]) == 0
            assert main(["remove", "cli-ep", "--root", root]) == 0
            assert "removed cli-ep" in capsys.readouterr().out
            assert main(["list", "--root", root]) == 0
            assert capsys.readouterr().out == ""
    finally:
        server.shutdown()


def test_add_root_scan_register_flow(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        models_root = Path(tmp) / "models"
        _write_local_model(models_root, "cli-scanned")
        root = str(Path(tmp) / "root")
        assert main(["add-root", str(models_root), "--root", root]) == 0
        assert f"root: {models_root}" in capsys.readouterr().out
        assert main(["scan", "--root", root]) == 0
        assert "cli-scanned | inspected" in capsys.readouterr().out
        assert main(["register", str(models_root / "cli-scanned"), "--root", root]) == 0
        assert "registered cli-scanned (ready)" in capsys.readouterr().out


def test_resolve_no_match_is_a_runtime_error(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp) / "root")
        assert main(["resolve", "vision", "--root", root]) == 1
        assert "no vision model" in capsys.readouterr().err


def test_unknown_model_operations_error(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp) / "root")
        for argv in (["load", "ghost"], ["unload", "ghost"], ["remove", "ghost"]):
            assert main([*argv, "--root", root]) == 1
            assert "unknown model" in capsys.readouterr().err


def test_usage_error_exits_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["download"])  # missing required url
    assert excinfo.value.code == 2


def test_bad_catalog_file_is_a_runtime_error(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.json"
        bad.write_text("{ nope", encoding="utf-8")
        assert main(["search", "--catalog", str(bad)]) == 1
        assert "JSON" in capsys.readouterr().err


def test_inspect_unreachable_url_errors(capsys):
    server, url, _, _ = make_file_server("gone")
    server.shutdown()
    with tempfile.TemporaryDirectory() as tmp:
        assert main(["inspect", url, "--root", str(Path(tmp) / "root")]) == 1
        assert "cannot fetch" in capsys.readouterr().err
