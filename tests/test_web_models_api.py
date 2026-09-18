"""HTTP contract for the Model Manager API: ok-envelope payloads over a live
threaded server, with tempfile.TemporaryDirectory as the models root.
"""

import http.client
import json
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pytest

from haven.models.manifest import ModelManifest, manifest_filename
from haven.web.server import make_server

from models_stub_http import make_file_server, make_manifest_dict
from test_web_server import _get_json, _post


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        models_root = Path(tmp) / "models-root"
        instance, _ = make_server(0, models_root=models_root)
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        yield instance, instance.server_address[1], Path(tmp), models_root
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _write_local_model(parent: Path, model_id: str, **manifest_kwargs) -> Path:
    manifest = ModelManifest.from_dict(make_manifest_dict(model_id, **manifest_kwargs))
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"local weights")
    manifest.save(folder / manifest_filename())
    return folder


def _write_broken_model(parent: Path, model_id: str) -> Path:
    data = make_manifest_dict(model_id)
    data["sha256"] = {"weights.bin": "f" * 64}
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"local weights")
    ModelManifest.from_dict(data).save(folder / manifest_filename())
    return folder


def _row(body: dict, model_id: str) -> dict:
    return next(row for row in body["models"] if row["id"] == model_id)


def _post_raw(port: int, path: str, raw_body: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, body=raw_body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    status = response.status
    connection.close()
    return status, json.loads(body)


def test_get_models_empty_lists_models_roots_catalog_backends_and_assignments(server) -> None:
    _, port, _, _ = server

    status, body = _get_json(port, "/api/models")

    assert status == 200
    assert body["ok"] is True
    assert body["models"] == []
    assert body["roots"] == []
    assert body["catalog"] == []
    # every role is present, assigned or null
    assert body["assignments"] == {
        "chat": None,
        "asr": None,
        "tts": None,
        "wake_word": None,
        "vad": None,
        "vision": None,
    }
    # the default reference set reports per-backend availability
    backends = {row["backend"]: row for row in body["backends"]}
    assert set(backends) == {"http", "transformers", "llama_cpp", "onnx", "piper"}
    assert backends["http"] == {
        "backend": "http",
        "available": True,
        "detail": "stdlib backend; always available",
    }
    for name in ("transformers", "llama_cpp", "onnx", "piper"):
        assert set(backends[name]) == {"backend", "available", "detail"}
        assert backends[name]["backend"] == name


def test_install_local_then_get_shows_ready_with_full_row_shape(server) -> None:
    _, port, tmp, models_root = server
    folder = _write_local_model(tmp, "local-model", languages=("en", "fr"))

    status, body = _post(port, "/api/models/install-local", {"folder": str(folder)})

    assert status == 200
    assert body["ok"] is True
    row = _row(body, "local-model")
    assert set(row) == {
        "id", "kind", "state", "source", "capabilities", "backend", "architecture",
        "version", "license", "languages", "registered_at", "loaded_backend",
    }
    assert row == {
        "id": "local-model",
        "kind": "intelligence",
        "state": "ready",
        "source": "local",
        "capabilities": ["chat"],
        "backend": "fake",
        "architecture": "stub",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "languages": ["en", "fr"],
        "registered_at": row["registered_at"],
        "loaded_backend": None,
    }
    assert datetime.fromisoformat(row["registered_at"]).tzinfo is not None
    assert body["roots"] == []

    status, body = _get_json(port, "/api/models")
    assert status == 200
    assert _row(body, "local-model")["state"] == "ready"
    assert (models_root / "intelligence" / "local-model" / "weights.bin").is_file()


def test_inspect_returns_inspection_without_installing(server) -> None:
    _, port, _, models_root = server
    stub, url, _, _ = make_file_server("inspect-me")
    try:
        status, body = _post(port, "/api/models/inspect", {"url": url})

        assert status == 200
        assert body["ok"] is True
        assert body["inspection"] == {
            "id": "inspect-me",
            "kind": "intelligence",
            "backend": "fake",
            "architecture": "stub",
            "version": "1.0.0",
            "license": "Apache-2.0",
            "languages": ["en"],
            "file_count": 2,
            "hash_verification": True,
            "remote_code": "none",
        }
        # inspect NEVER downloads weights: nothing landed in storage
        assert body.get("models") is None
        assert not models_root.exists() or not any(models_root.iterdir())
    finally:
        stub.shutdown()


def test_install_url_from_stub_serves_manifest_and_files(server) -> None:
    _, port, _, models_root = server
    stub, url, _, _ = make_file_server("url-model")
    try:
        status, body = _post(port, "/api/models/install-url", {"url": url})

        assert status == 200
        assert body["ok"] is True
        row = _row(body, "url-model")
        assert row["state"] == "ready"
        assert row["source"] == "downloaded"
        assert (models_root / "intelligence" / "url-model" / "weights.bin").read_bytes() == b"stub weights bytes"
    finally:
        stub.shutdown()


def test_add_root_scan_classifies_broken_and_register_activates(server) -> None:
    _, port, tmp, _ = server
    scan_root = tmp / "scan-root"
    good = _write_local_model(scan_root, "good-model")
    bad = _write_broken_model(scan_root, "broken-model")

    status, body = _post(port, "/api/models/add-root", {"path": str(scan_root)})
    assert status == 200
    assert body["ok"] is True
    assert body["roots"] == [str(scan_root)]

    status, body = _post(port, "/api/models/scan", {})
    assert status == 200
    assert body["ok"] is True
    assert body["roots"] == [str(scan_root)]
    discovered = {Path(row["path"]).name: row for row in body["discovered"]}
    assert set(discovered) == {"good-model", "broken-model"}
    assert discovered["good-model"]["state"] == "inspected"
    assert discovered["good-model"]["problems"] == []
    assert discovered["good-model"]["id"] == "good-model"
    assert discovered["good-model"]["kind"] == "intelligence"
    assert discovered["broken-model"]["state"] == "hash_mismatch"
    assert discovered["broken-model"]["problems"]
    assert discovered["broken-model"]["id"] == "broken-model"
    # scan never auto-activates
    assert body["models"] == []

    status, body = _post(port, "/api/models/register", {"path": str(good)})
    assert status == 200
    assert body["ok"] is True
    assert _row(body, "good-model")["state"] == "ready"
    assert _row(body, "good-model")["source"] == "local"

    # registering the broken folder fails as ok:false with the error string
    status, body = _post(port, "/api/models/register", {"path": str(bad)})
    assert status == 200
    assert body["ok"] is False
    assert "weights.bin" in body["error"]


def test_load_without_backend_records_state_and_remove_clears_it(server) -> None:
    _, port, tmp, models_root = server
    folder = _write_local_model(tmp, "nobackend", backend="unregistered-backend")
    _, body = _post(port, "/api/models/install-local", {"folder": str(folder)})
    assert _row(body, "nobackend")["state"] == "ready"

    status, body = _post(port, "/api/models/load", {"id": "nobackend"})
    assert status == 200
    assert body["ok"] is False
    assert "unregistered-backend" in body["error"]
    # the failure is recorded on the record and reflected in the lists
    assert _row(body, "nobackend")["state"] == "backend_missing"
    assert body["roots"] == []

    status, body = _post(port, "/api/models/unload", {"id": "nobackend"})
    assert status == 200
    assert body["ok"] is True
    assert _row(body, "nobackend")["state"] == "backend_missing"

    status, body = _post(port, "/api/models/remove", {"id": "nobackend"})
    assert status == 200
    assert body["ok"] is True
    assert body["models"] == []
    assert not (models_root / "intelligence" / "nobackend").exists()

    status, body = _post(port, "/api/models/load", {"id": "nope"})
    assert status == 200
    assert body["ok"] is False
    assert "unknown model" in body["error"]


def test_add_endpoint_registers_an_endpoint_source_row(server) -> None:
    _, port, _, _ = server
    stub, url, _, _ = make_file_server("ep-model")
    try:
        status, body = _post(port, "/api/models/add-endpoint", {"url": url})

        assert status == 200
        assert body["ok"] is True
        row = _row(body, "ep-model")
        assert row["state"] == "ready"
        assert row["source"] == "endpoint"

        _, body = _get_json(port, "/api/models")
        assert _row(body, "ep-model")["source"] == "endpoint"
    finally:
        stub.shutdown()


def test_malformed_json_returns_400(server) -> None:
    _, port, _, _ = server

    status, body = _post_raw(port, "/api/models/install-url", "not json")

    assert status == 400
    assert body == {"error": "invalid JSON body"}


def test_missing_required_field_is_an_ok_false_envelope(server) -> None:
    _, port, _, _ = server

    status, body = _post(port, "/api/models/install-url", {})

    assert status == 200
    assert body == {"ok": False, "error": "a non-empty 'url' is required"}


def test_models_root_env_var_overrides_the_passed_default(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("HAVEN_MODELS_ROOT", tmp)
        instance, _ = make_server(0, models_root=Path(tmp) / "ignored")
        try:
            assert instance.models.models_root == Path(tmp)
        finally:
            instance.server_close()


# -- role assignments over the API -------------------------------------------------


def test_assign_binds_a_model_and_the_get_payload_reflects_it(server) -> None:
    _, port, tmp, _ = server
    folder = _write_local_model(tmp, "chat-model")

    status, body = _post(port, "/api/models/install-local", {"folder": str(folder)})
    assert status == 200 and body["ok"] is True

    status, body = _post(port, "/api/models/assign", {"role": "chat", "id": "chat-model"})

    assert status == 200
    assert body["ok"] is True
    # the assign answers with the same models+backends refetch shape
    assert body["assignments"]["chat"] == "chat-model"
    assert _row(body, "chat-model")["state"] == "ready"
    assert any(row["backend"] == "http" for row in body["backends"])

    _, body = _get_json(port, "/api/models")
    assert body["assignments"]["chat"] == "chat-model"


def test_assign_null_id_clears_the_assignment(server) -> None:
    _, port, tmp, _ = server
    folder = _write_local_model(tmp, "chat-model")
    _post(port, "/api/models/install-local", {"folder": str(folder)})
    _post(port, "/api/models/assign", {"role": "chat", "id": "chat-model"})

    status, body = _post(port, "/api/models/assign", {"role": "chat", "id": None})

    assert status == 200
    assert body["ok"] is True
    assert body["assignments"]["chat"] is None


def test_assign_rejects_unknown_role_unknown_model_and_wrong_fit(server) -> None:
    _, port, tmp, _ = server
    folder = _write_local_model(tmp, "chat-model")
    _post(port, "/api/models/install-local", {"folder": str(folder)})

    status, body = _post(port, "/api/models/assign", {"role": "narrator", "id": "chat-model"})
    assert status == 200
    assert body["ok"] is False
    assert "unknown role" in body["error"]

    status, body = _post(port, "/api/models/assign", {"role": "chat", "id": "ghost-model"})
    assert status == 200
    assert body["ok"] is False
    assert "unknown model" in body["error"]

    # a chat model cannot serve the asr role: kind/capability fit fails
    status, body = _post(port, "/api/models/assign", {"role": "asr", "id": "chat-model"})
    assert status == 200
    assert body["ok"] is False
    assert "cannot serve role" in body["error"]


def test_assign_requires_a_role_field(server) -> None:
    _, port, _, _ = server

    status, body = _post(port, "/api/models/assign", {"id": "anything"})

    assert status == 200
    assert body == {"ok": False, "error": "a non-empty 'role' is required"}
