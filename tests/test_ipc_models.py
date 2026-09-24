"""Native models IPC adapter: the full Model Manager surface over the dispatcher."""

from __future__ import annotations

import json
import socket
import tempfile
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.web.server import make_server

from models_stub_http import make_file_server, make_manifest_dict
from test_models_jobs import JobStub, _CONFIG, _manifest_dict, _wait_for
from test_web_models_api import _write_broken_model, _write_local_model


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        models_root = Path(tmp) / "models-root"
        instance, _ = make_server(0, models_root=models_root)
        try:
            yield instance, Path(tmp), models_root
        finally:
            instance.server_close()


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _row(body: dict, model_id: str) -> dict:
    return next(row for row in body["models"] if row["id"] == model_id)


def _serve_model(model_id: str, *, slow: bool = False) -> tuple[JobStub, str]:
    subdir = f"/models/{model_id}"
    files = {"weights.bin": b"ipc job weights " * 2048, "config.json": _CONFIG}
    manifest = _manifest_dict(model_id, files, subdir)
    served = {f"{subdir}/haven-model.json": json.dumps(manifest).encode("utf-8")}
    served.update({f"{subdir}/{name}": data for name, data in files.items()})
    stub = JobStub(served, slow=(f"{subdir}/weights.bin",) if slow else ())
    return stub, stub.url(f"{subdir}/haven-model.json")


def test_list_shape_and_overview_backward_compatibility(server) -> None:
    instance, _, _ = server

    listed = _dispatch(instance, "models.list", {})
    assert listed["ok"] is True
    assert listed["result"]["ok"] is True
    assert listed["result"]["models"] == []
    assert listed["result"]["roots"] == []
    assert listed["result"]["assignments"] == {
        "chat": None,
        "asr": None,
        "tts": None,
        "wake_word": None,
        "vad": None,
        "vision": None,
    }
    backends = {row["backend"]: row for row in listed["result"]["backends"]}
    assert backends["http"]["available"] is True

    overview = _dispatch(instance, "models.overview", {})
    assert overview["ok"] is True
    assert "catalog" in overview["result"]


def test_install_local_shows_ready_and_inspect_never_installs(server) -> None:
    instance, tmp, models_root = server
    folder = _write_local_model(tmp, "ipc-model", languages=("en", "fr"))

    installed = _dispatch(instance, "models.install_local", {"folder": str(folder)})
    assert installed["ok"] is True
    assert installed["result"]["ok"] is True
    row = _row(installed["result"], "ipc-model")
    assert row["state"] == "ready"
    assert row["source"] == "local"
    assert row["capabilities"] == ["chat"]
    assert (models_root / "intelligence" / "ipc-model" / "weights.bin").is_file()

    stub, url, _, _ = make_file_server("inspect-me")
    try:
        inspected = _dispatch(instance, "models.inspect", {"url": url})
        assert inspected["result"]["ok"] is True
        inspection = inspected["result"]["inspection"]
        assert inspection["id"] == "inspect-me"
        assert inspection["hash_verification"] is True
        assert inspection["remote_code"] == "none"
        # inspect NEVER downloads weights: nothing for this model landed in storage
        assert not (models_root / "intelligence" / "inspect-me").exists()
        assert [row["id"] for row in _dispatch(instance, "models.list", {})["result"]["models"]] == ["ipc-model"]
    finally:
        stub.shutdown()

    missing = _dispatch(instance, "models.install_url", {})
    assert missing["ok"] is False
    assert missing["error"] == "a non-empty 'url' is required"


def test_install_url_and_add_endpoint_register_rows(server) -> None:
    instance, _, models_root = server
    stub, url, _, _ = make_file_server("url-model")
    try:
        installed = _dispatch(instance, "models.install_url", {"url": url})
        assert installed["result"]["ok"] is True
        row = _row(installed["result"], "url-model")
        assert row["state"] == "ready"
        assert row["source"] == "downloaded"
        assert (models_root / "intelligence" / "url-model" / "weights.bin").is_file()
    finally:
        stub.shutdown()

    stub, url, _, _ = make_file_server("ep-model")
    try:
        registered = _dispatch(instance, "models.add_endpoint", {"url": url})
        assert registered["result"]["ok"] is True
        assert _row(registered["result"], "ep-model")["source"] == "endpoint"
    finally:
        stub.shutdown()


def test_scan_classifies_candidates_and_register_is_the_activation_gate(server) -> None:
    instance, tmp, _ = server
    scan_root = tmp / "scan-root"
    good = _write_local_model(scan_root, "good-model")
    bad = _write_broken_model(scan_root, "broken-model")

    added = _dispatch(instance, "models.add_root", {"path": str(scan_root)})
    assert added["result"]["ok"] is True
    assert added["result"]["roots"] == [str(scan_root)]

    scanned = _dispatch(instance, "models.scan", {})
    assert scanned["result"]["ok"] is True
    discovered = {Path(row["path"]).name: row for row in scanned["result"]["discovered"]}
    assert discovered["good-model"]["state"] == "inspected"
    assert discovered["good-model"]["problems"] == []
    assert discovered["broken-model"]["state"] == "hash_mismatch"
    assert discovered["broken-model"]["problems"]
    # scan never auto-activates
    assert scanned["result"]["models"] == []

    registered = _dispatch(instance, "models.register", {"path": str(good)})
    assert registered["result"]["ok"] is True
    row = _row(registered["result"], "good-model")
    assert row["state"] == "ready"
    assert row["source"] == "local"

    refused = _dispatch(instance, "models.register", {"path": str(bad)})
    assert refused["result"]["ok"] is False
    assert "weights.bin" in refused["result"]["error"]


def test_load_failure_is_recorded_and_remove_clears_it(server) -> None:
    instance, tmp, models_root = server
    folder = _write_local_model(tmp, "nobackend", backend="unregistered-backend")
    _dispatch(instance, "models.install_local", {"folder": str(folder)})

    failed = _dispatch(instance, "models.load", {"id": "nobackend"})
    assert failed["result"]["ok"] is False
    assert "unregistered-backend" in failed["result"]["error"]
    # the failure is recorded on the record and carried in the envelope
    assert _row(failed["result"], "nobackend")["state"] == "backend_missing"
    assert failed["result"]["roots"] == []

    unloaded = _dispatch(instance, "models.unload", {"id": "nobackend"})
    assert unloaded["result"]["ok"] is True
    assert _row(unloaded["result"], "nobackend")["state"] == "backend_missing"

    removed = _dispatch(instance, "models.remove", {"id": "nobackend"})
    assert removed["result"]["ok"] is True
    assert removed["result"]["models"] == []
    assert not (models_root / "intelligence" / "nobackend").exists()

    unknown = _dispatch(instance, "models.load", {"id": "nope"})
    assert unknown["result"]["ok"] is False
    assert "unknown model" in unknown["result"]["error"]


def test_assign_binds_clears_and_rejects_bad_fits(server) -> None:
    instance, tmp, _ = server
    folder = _write_local_model(tmp, "chat-model")
    _dispatch(instance, "models.install_local", {"folder": str(folder)})

    assigned = _dispatch(instance, "models.assign", {"role": "chat", "id": "chat-model"})
    assert assigned["result"]["ok"] is True
    assert assigned["result"]["assignments"]["chat"] == "chat-model"

    cleared = _dispatch(instance, "models.assign", {"role": "chat", "id": None})
    assert cleared["result"]["ok"] is True
    assert cleared["result"]["assignments"]["chat"] is None

    bad_role = _dispatch(instance, "models.assign", {"role": "narrator", "id": "chat-model"})
    assert bad_role["result"]["ok"] is False
    assert "unknown role" in bad_role["result"]["error"]

    ghost = _dispatch(instance, "models.assign", {"role": "chat", "id": "ghost-model"})
    assert ghost["result"]["ok"] is False
    assert "unknown model" in ghost["result"]["error"]

    wrong_fit = _dispatch(instance, "models.assign", {"role": "asr", "id": "chat-model"})
    assert wrong_fit["result"]["ok"] is False
    assert "cannot serve role" in wrong_fit["result"]["error"]

    missing_role = _dispatch(instance, "models.assign", {"id": "chat-model"})
    assert missing_role["ok"] is False
    assert missing_role["error"] == "a non-empty 'role' is required"


def test_download_job_lifecycle_over_ipc(server) -> None:
    instance, _, models_root = server
    stub, url = _serve_model("ipc-job-ok")
    try:
        started = _dispatch(instance, "models.download", {"url": url})
        assert started["result"]["ok"] is True
        job_id = started["result"]["job_id"]

        listed = _dispatch(instance, "models.jobs", {})
        assert listed["result"]["ok"] is True
        (job,) = listed["result"]["jobs"]
        assert job["job_id"] == job_id
        assert job["manifest_id"] == "ipc-job-ok"
        assert set(job) == {
            "job_id", "url", "state", "received_bytes", "total_bytes",
            "current_file", "error", "manifest_id", "created_at", "updated_at",
        }

        _wait_for(
            lambda: next(
                item for item in _dispatch(instance, "models.jobs", {})["result"]["jobs"]
                if item["job_id"] == job_id
            )["state"] == "ready"
        )
        models = _dispatch(instance, "models.list", {})["result"]["models"]
        row = next(item for item in models if item["id"] == "ipc-job-ok")
        assert row["state"] == "ready"
        assert row["source"] == "downloaded"
        assert (models_root / "intelligence" / "ipc-job-ok" / "weights.bin").is_file()
    finally:
        stub.shutdown()


def test_download_resolution_failure_and_cancel_and_unknown_job(server) -> None:
    instance, _, _ = server
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    failed = _dispatch(
        instance, "models.download", {"url": f"http://127.0.0.1:{dead_port}/m/x/haven-model.json"}
    )
    assert failed["result"]["ok"] is False
    assert failed["result"]["error"]
    assert "job_id" not in failed["result"]
    (job,) = _dispatch(instance, "models.jobs", {})["result"]["jobs"]
    assert job["state"] == "failed"
    assert job["error"] == failed["result"]["error"]

    unknown_cancel = _dispatch(instance, "models.job.cancel", {"job_id": "nope"})
    assert unknown_cancel["result"] == {"ok": False, "error": "unknown job: nope"}

    stub, url = _serve_model("ipc-job-cancel", slow=True)
    try:
        started = _dispatch(instance, "models.download", {"url": url})
        job_id = started["result"]["job_id"]
        _wait_for(
            lambda: next(
                item for item in _dispatch(instance, "models.jobs", {})["result"]["jobs"]
                if item["job_id"] == job_id
            )["state"] == "downloading"
        )
        cancelled = _dispatch(instance, "models.job.cancel", {"job_id": job_id})
        assert cancelled["result"]["ok"] is True
        assert cancelled["result"]["job"]["state"] in ("downloading", "cancelled")
        _wait_for(
            lambda: next(
                item for item in _dispatch(instance, "models.jobs", {})["result"]["jobs"]
                if item["job_id"] == job_id
            )["state"] == "cancelled"
        )
    finally:
        stub.shutdown()
