"""HTTP contract for background model-download jobs: the /api/models/download
start endpoint, job list/detail/cancel envelopes, the /api/models/events SSE
stream (initial jobs list + model_job transitions), resolution-failure
envelopes, and the stock-wiring regression (a bare ModelManager resolves the
http backend).
"""

import http.client
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from haven.models import ModelManager
from haven.web.server import make_server

from test_web_server import _get_json, _post, _read_event

from test_models_jobs import JobStub, _CONFIG, _manifest_dict, _wait_for


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


def _serve_model(model_id: str, *, slow: bool = False) -> tuple[JobStub, str]:
    subdir = f"/models/{model_id}"
    files = {"weights.bin": b"web job weights " * 2048, "config.json": _CONFIG}
    manifest = _manifest_dict(model_id, files, subdir)
    served = {f"{subdir}/haven-model.json": json.dumps(manifest).encode("utf-8")}
    served.update({f"{subdir}/{name}": data for name, data in files.items()})
    stub = JobStub(served, slow=(f"{subdir}/weights.bin",) if slow else ())
    return stub, stub.url(f"{subdir}/haven-model.json")


def _job(server, job_id: str) -> dict:
    _, port, _, _ = server
    status, body = _get_json(port, f"/api/models/jobs/{job_id}")
    assert status == 200
    assert body["ok"] is True
    return body["job"]


def test_download_starts_job_and_list_detail_and_model_ready(server) -> None:
    _, port, _, models_root = server
    stub, url = _serve_model("web-job-ok")
    try:
        status, body = _post(port, "/api/models/download", {"url": url})
        assert status == 200
        assert body["ok"] is True
        job_id = body["job_id"]
        assert job_id

        status, body = _get_json(port, "/api/models/jobs")
        assert status == 200
        assert body["ok"] is True
        (job,) = body["jobs"]
        assert job["job_id"] == job_id
        assert set(job) == {
            "job_id", "url", "state", "received_bytes", "total_bytes",
            "current_file", "error", "manifest_id", "created_at", "updated_at",
        }
        assert job["url"] == url
        assert job["manifest_id"] == "web-job-ok"

        _wait_for(lambda: _job(server, job_id)["state"] == "ready")
        job = _job(server, job_id)
        assert job["error"] is None
        assert job["state"] == "ready"

        status, body = _get_json(port, "/api/models")
        assert status == 200
        row = next(row for row in body["models"] if row["id"] == "web-job-ok")
        assert row["state"] == "ready"
        assert row["source"] == "downloaded"
        assert (models_root / "intelligence" / "web-job-ok" / "weights.bin").is_file()
    finally:
        stub.shutdown()


def test_download_resolution_failure_returns_ok_false_without_a_job(server) -> None:
    _, port, _, _ = server
    probe = __import__("socket").socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()  # nobody listens there; the resolution is refused

    status, body = _post(
        port, "/api/models/download", {"url": f"http://127.0.0.1:{dead_port}/m/x/haven-model.json"}
    )

    assert status == 200
    assert body["ok"] is False
    assert body["error"]
    assert "job_id" not in body
    # the failed job is still listed so the UI renders it uniformly
    _, listed = _get_json(port, "/api/models/jobs")
    assert listed["ok"] is True
    (job,) = listed["jobs"]
    assert job["state"] == "failed"
    assert job["error"] == body["error"]


def test_unknown_job_detail_and_cancel_return_ok_false(server) -> None:
    _, port, _, _ = server

    status, body = _get_json(port, "/api/models/jobs/nope")
    assert status == 200
    assert body == {"ok": False, "error": "unknown job: nope"}

    status, body = _post(port, "/api/models/jobs/nope/cancel", {})
    assert status == 200
    assert body == {"ok": False, "error": "unknown job: nope"}


def test_cancel_endpoint_cancels_a_running_job(server) -> None:
    _, port, _, _ = server
    stub, url = _serve_model("web-job-cancel", slow=True)
    try:
        _, body = _post(port, "/api/models/download", {"url": url})
        job_id = body["job_id"]
        _wait_for(lambda: _job(server, job_id)["state"] == "downloading")

        status, body = _post(port, f"/api/models/jobs/{job_id}/cancel", {})
        assert status == 200
        assert body["ok"] is True
        # cancellation is cooperative: the worker flips the job to cancelled
        # once the in-flight download observes the flag between chunks
        _wait_for(lambda: _job(server, job_id)["state"] == "cancelled")
        assert _job(server, job_id)["state"] == "cancelled"
    finally:
        stub.shutdown()


def test_sse_streams_jobs_then_model_job_transitions(server) -> None:
    _, port, _, _ = server
    stub, url = _serve_model("web-job-sse")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    connection.request("GET", "/api/models/events")
    response = connection.getresponse()
    try:
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream"

        event, data = _read_event(response)
        assert event == "jobs"
        assert json.loads(data) == []  # no jobs yet: the full list on connect

        status, body = _post(port, "/api/models/download", {"url": url})
        assert status == 200
        job_id = body["job_id"]

        seen: list[str] = []
        for _ in range(10):
            event, data = _read_event(response)
            assert event == "model_job"
            job = json.loads(data)
            assert job["job_id"] == job_id
            seen.append(job["state"])
            if job["state"] == "ready":
                break
        assert seen[0] in ("queued", "downloading")
        assert "ready" in seen
        assert seen[-1] == "ready"
    finally:
        connection.close()
        stub.shutdown()


def test_stock_manager_resolves_the_http_backend() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = ModelManager(Path(tmp) / "root")
        assert manager._backends.resolve("http") is not None
        # the lazy reference loaders are registered too (they gate at load)
        for name in ("transformers", "llama_cpp", "onnx"):
            assert manager._backends.resolve(name) is not None
