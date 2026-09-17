"""Background download jobs over a stub HTTP server: lifecycle transitions
observed via subscribe, byte totals from Content-Length, cooperative cancel
mid-download with .part cleanup, Range/206 resume of a pre-seeded .part,
hash-verify failure as a failed job, the happy path registering the model
through the manager, and the two-concurrent-download queue cap.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from haven.models import (
    DownloadJobManager,
    JobState,
    ModelManager,
    ModelState,
    download_files,
)
from haven.models.downloader import DownloadCancelledError
from haven.models.manifest import SCHEMA_VERSION, ModelManifest

_WEIGHTS = b"job weights bytes " * 2048  # ~36 KiB, many chunks when throttled
_CONFIG = b'{"layers": 1}'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_GET(self):
        self.server.requests.append(
            {"path": self.path, "range": self.headers.get("Range")}
        )
        body = self.server.files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        range_header = self.headers.get("Range")
        if range_header and self.server.ranges:
            start = int(range_header.removeprefix("bytes=").split("-")[0])
            chunk = body[start:]
            self.send_response(206)
            self.server.requests[-1]["status"] = 206
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.server.requests[-1]["status"] = 200
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.path in self.server.slow_paths:
            view = memoryview(body)
            for offset in range(0, len(body), 1024):
                self.wfile.write(view[offset : offset + 1024])
                self.wfile.flush()
                time.sleep(0.01)
            return
        self.wfile.write(body)


class JobStub:
    """Stub server with Range/206 support, slow streaming, and a request log."""

    def __init__(self, files: dict[str, bytes], *, ranges: bool = True, slow: tuple[str, ...] = ()):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.files = dict(files)
        self._server.ranges = ranges
        self._server.slow_paths = tuple(slow)
        self._server.requests = []
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def requests(self) -> list[dict]:
        return self._server.requests

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _manifest_dict(model_id: str, files: dict[str, bytes], subdir: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": model_id,
        "version": "1.0.0",
        "kind": "intelligence",
        "capabilities": ["chat"],
        "architecture": "stub",
        "backend": "fake",
        "files": {"weights": "weights.bin", "config": "config.json"},
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "languages": ["en"],
        "hardware": {"cpu": True},
        "license": "Apache-2.0",
        "source": f"{subdir}/haven-model.json",
    }


def _serve_models(model_ids: list[str], *, slow: bool = False) -> tuple[JobStub, dict[str, str]]:
    """One stub serving `haven-model.json` + files per model id; returns {id: url}."""

    served: dict[str, bytes] = {}
    urls: dict[str, str] = {}
    for model_id in model_ids:
        subdir = f"/models/{model_id}"
        files = {"weights.bin": _WEIGHTS, "config.json": _CONFIG}
        manifest = _manifest_dict(model_id, files, subdir)
        served[f"{subdir}/haven-model.json"] = json.dumps(manifest).encode("utf-8")
        served.update({f"{subdir}/{name}": data for name, data in files.items()})
    stub = JobStub(
        served,
        slow=tuple(f"/models/{model_id}/weights.bin" for model_id in model_ids) if slow else (),
    )
    for model_id in model_ids:
        urls[model_id] = stub.url(f"/models/{model_id}/haven-model.json")
    return stub, urls


def _serve_model(model_id: str, *, slow: bool = False, subdir: str | None = None) -> tuple[JobStub, str, dict]:
    subdir = subdir or f"/models/{model_id}"
    files = {"weights.bin": _WEIGHTS, "config.json": _CONFIG}
    manifest = _manifest_dict(model_id, files, subdir)
    served = {f"{subdir}/haven-model.json": json.dumps(manifest).encode("utf-8")}
    served.update({f"{subdir}/{name}": data for name, data in files.items()})
    stub = JobStub(served, slow=(f"{subdir}/weights.bin",) if slow else ())
    return stub, stub.url(f"{subdir}/haven-model.json"), manifest


def _manager(tmp: str) -> ModelManager:
    return ModelManager(Path(tmp) / "root")


def _wait_for(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met before the deadline")


def test_job_lifecycle_transitions_and_registers_model():
    stub, url, manifest = _serve_model("job-ok")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            jobs = DownloadJobManager(manager)
            seen: list[JobState] = []
            jobs.subscribe(lambda job: seen.append(job.state))
            job_id = jobs.start(url)
            assert job_id
            _wait_for(lambda: jobs.status(job_id).state is JobState.READY)
            assert seen[0] is JobState.QUEUED
            assert seen[-1] is JobState.READY
            assert {JobState.DOWNLOADING, JobState.VERIFYING} <= set(seen)
            job = jobs.status(job_id)
            assert job.manifest_id == "job-ok"
            assert job.error is None
            assert job.received_bytes == len(_WEIGHTS) + len(_CONFIG)
            assert job.total_bytes == len(_WEIGHTS) + len(_CONFIG)
            assert job.current_file == "config.json"
            assert job.created_at.tzinfo is not None
            assert job.updated_at.tzinfo is not None
            record = manager.get("job-ok")
            assert record is not None
            assert record.state is ModelState.READY
            assert record.source_type.value == "downloaded"
            assert (manager.models_root / "intelligence" / "job-ok" / "weights.bin").is_file()
    finally:
        stub.shutdown()


def test_progress_reports_byte_totals_from_content_length():
    big = b"0123456789abcdef" * 16384  # 256 KiB: several 64 KiB read windows
    subdir = "/models/job-bytes"
    files = {"weights.bin": big, "config.json": _CONFIG}
    manifest = _manifest_dict("job-bytes", files, subdir)
    served = {f"{subdir}/haven-model.json": json.dumps(manifest).encode("utf-8")}
    served.update({f"{subdir}/{name}": data for name, data in files.items()})
    stub = JobStub(served, slow=(f"{subdir}/weights.bin",))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            jobs = DownloadJobManager(_manager(tmp))
            job_id = jobs.start(stub.url(f"{subdir}/haven-model.json"))
            _wait_for(
                lambda: jobs.status(job_id).total_bytes == len(big)
                and 0 < jobs.status(job_id).received_bytes < len(big)
            )
            job = jobs.status(job_id)
            assert job.current_file == "weights.bin"
            jobs.cancel(job_id)
            _wait_for(lambda: jobs.status(job_id).state is JobState.CANCELLED)
    finally:
        stub.shutdown()


def test_cancel_mid_download_marks_cancelled_and_leaves_no_install():
    stub, url, _ = _serve_model("job-cancel", slow=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            jobs = DownloadJobManager(manager)
            job_id = jobs.start(url)
            _wait_for(lambda: jobs.status(job_id).state is JobState.DOWNLOADING)
            assert jobs.cancel(job_id) is True
            _wait_for(lambda: jobs.status(job_id).state is JobState.CANCELLED)
            job = jobs.status(job_id)
            assert job.error is None
            # nothing was installed; the record stays explicit, not ready
            assert manager.get("job-cancel") is not None
            assert manager.get("job-cancel").state is not ModelState.READY
            assert not (manager.models_root / "intelligence" / "job-cancel").exists()
            # the per-job staging dir was removed with it
            assert not (manager.models_root / ".staging" / "job-cancel").exists()
            # cancelling a terminal job is a no-op
            assert jobs.cancel(job_id) is False
    finally:
        stub.shutdown()


def test_cancel_before_start_marks_queued_job_cancelled():
    stub, urls = _serve_models(["pre-cancel-1", "pre-cancel-2", "pre-cancel-3"], slow=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            jobs = DownloadJobManager(_manager(tmp))
            # occupy both slots so the third job stays queued
            first = jobs.start(urls["pre-cancel-1"])
            second = jobs.start(urls["pre-cancel-2"])
            _wait_for(lambda: jobs.status(first).state is JobState.DOWNLOADING)
            third_id = jobs.start(urls["pre-cancel-3"])
            assert jobs.cancel(third_id) is True
            _wait_for(lambda: jobs.status(third_id).state is JobState.CANCELLED)
            assert jobs.status(third_id).error is None
            jobs.cancel(first)
            jobs.cancel(second)
            _wait_for(
                lambda: jobs.status(first).state is JobState.CANCELLED
                and jobs.status(second).state is JobState.CANCELLED
            )
    finally:
        stub.shutdown()


def test_resolution_failure_becomes_a_failed_job_not_a_raise():
    probe = __import__("socket").socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # the port is now nobody's; connecting must be refused
    with tempfile.TemporaryDirectory() as tmp:
        jobs = DownloadJobManager(_manager(tmp))
        job_id = jobs.start(f"http://127.0.0.1:{port}/models/x/haven-model.json")
        job = jobs.status(job_id)
        assert job.state is JobState.FAILED
        assert job.error
        assert job.manifest_id is None
        with pytest.raises(KeyError):
            jobs.status("no-such-job")
        assert jobs.cancel("no-such-job") is False


def test_verify_failure_marks_job_failed_with_hash_error():
    stub, url, manifest = _serve_model("job-bad")
    manifest["sha256"]["weights.bin"] = "0" * 64
    stub._server.files[f"/models/job-bad/haven-model.json"] = json.dumps(manifest).encode("utf-8")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            jobs = DownloadJobManager(manager)
            job_id = jobs.start(url)
            _wait_for(lambda: jobs.status(job_id).state is JobState.FAILED)
            job = jobs.status(job_id)
            assert "weights.bin" in (job.error or "")
            assert job.manifest_id == "job-bad"
            record = manager.get("job-bad")
            assert record is not None
            assert record.state is ModelState.HASH_MISMATCH
    finally:
        stub.shutdown()


def test_queue_caps_concurrent_downloads_at_two():
    stub, urls = _serve_models(["queue-1", "queue-2", "queue-3"], slow=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            jobs = DownloadJobManager(_manager(tmp))
            seen_peak = 0
            ids = [jobs.start(urls[model_id]) for model_id in ("queue-1", "queue-2", "queue-3")]
            _wait_for(lambda: sum(1 for j in jobs.list() if j.state is JobState.DOWNLOADING) == 2)
            while any(j.state in (JobState.QUEUED, JobState.DOWNLOADING) for j in jobs.list()):
                active = sum(1 for j in jobs.list() if j.state is JobState.DOWNLOADING)
                seen_peak = max(seen_peak, active)
                assert active <= 2
                time.sleep(0.02)
            seen_peak = max(seen_peak, 2)
            assert seen_peak == 2
            # every job ran to completion through the shared manager
            _wait_for(lambda: all(jobs.status(job_id).state is JobState.READY for job_id in ids))
    finally:
        stub.shutdown()


def test_job_resumes_a_preseeded_part_via_a_range_request():
    stub, url, _ = _serve_model("job-resume")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            # the job downloads into <models_root>/.staging/<manifest id>;
            # seed the weights .part with the first 1000 bytes before start
            staging = manager.models_root / ".staging" / "job-resume"
            staging.mkdir(parents=True)
            (staging / "weights.bin.part").write_bytes(_WEIGHTS[:1000])
            jobs = DownloadJobManager(manager)
            job_id = jobs.start(url)
            _wait_for(lambda: jobs.status(job_id).state is JobState.READY)
            job = jobs.status(job_id)
            assert job.received_bytes == len(_WEIGHTS) + len(_CONFIG)
            resumed = [r for r in stub.requests if r["range"]]
            assert resumed and all(r["range"] == "bytes=1000-" for r in resumed)
            installed = manager.models_root / "intelligence" / "job-resume" / "weights.bin"
            assert installed.read_bytes() == _WEIGHTS
            assert not staging.exists()
    finally:
        stub.shutdown()


def test_unsubscribe_stops_transition_callbacks():
    stub, url, _ = _serve_model("job-unsub")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            jobs = DownloadJobManager(_manager(tmp))
            seen: list[JobState] = []
            callback = lambda job: seen.append(job.state)
            jobs.subscribe(callback)
            job_id = jobs.start(url)
            jobs.unsubscribe(callback)
            _wait_for(lambda: jobs.status(job_id).state is JobState.READY)
            assert seen[0] is JobState.QUEUED
            assert JobState.READY not in seen
    finally:
        stub.shutdown()


# -- downloader-level cancel/resume (the .part contract) --------------------------


def test_download_files_cancel_raises_and_cleans_part():
    stub, url, manifest = _serve_model("dl-cancel", slow=True)
    try:
        parsed = ModelManifest.from_dict(manifest)
        ticks = {"n": 0}

        def cancel() -> bool:
            ticks["n"] += 1
            return ticks["n"] > 3

        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(DownloadCancelledError):
                download_files(parsed, base_url=url.rsplit("/", 1)[0] + "/", dest_dir=tmp, cancel=cancel)
            # the .part the call created was removed with the failure
            assert not (Path(tmp) / "weights.bin.part").exists()
            assert not (Path(tmp) / "weights.bin").exists()
    finally:
        stub.shutdown()


def test_download_files_resumes_a_preseeded_part_with_a_range_request():
    stub, url, manifest = _serve_model("dl-resume")
    try:
        parsed = ModelManifest.from_dict(manifest)
        head, tail = _WEIGHTS[:1000], _WEIGHTS[1000:]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "weights.bin.part").write_bytes(head)
            download_files(parsed, base_url=url.rsplit("/", 1)[0] + "/", dest_dir=dest)
            assert (dest / "weights.bin").read_bytes() == _WEIGHTS
            resumed = [r for r in stub.requests if r["range"]]
            assert resumed and all(r["range"] == "bytes=1000-" for r in resumed)
    finally:
        stub.shutdown()


def test_download_files_restarts_when_the_server_ignores_ranges():
    stub, url, manifest = _serve_model("dl-norange", subdir="/models/dl-norange")
    stub._server.ranges = False
    try:
        parsed = ModelManifest.from_dict(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "weights.bin.part").write_bytes(_WEIGHTS[:5])
            download_files(parsed, base_url=url.rsplit("/", 1)[0] + "/", dest_dir=dest)
            assert (dest / "weights.bin").read_bytes() == _WEIGHTS
            # the .part still earned a Range request, but a 200 (not 206)
            # answered it: the file restarted from scratch
            (weights_request,) = [r for r in stub.requests if r["path"].endswith("/weights.bin")]
            assert weights_request["range"] == "bytes=5-"
            assert weights_request["status"] == 200
    finally:
        stub.shutdown()
