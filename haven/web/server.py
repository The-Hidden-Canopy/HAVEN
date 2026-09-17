"""Stdlib HTTP surface for the HAVEN demo: JSON API, SSE stream, static files."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import queue
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..models import ModelManager, inspect_folder
from ..models.jobs import DownloadJobManager, job_to_dict
from ..models.storage import default_models_root
from .demo import Clock, DemoDirector
from .models_api import inspection_payload, models_payload, overview_payload, scan_payload

HEARTBEAT_SECONDS = 15

_MODELS_ROOT_ENV = "HAVEN_MODELS_ROOT"
# Lifecycle failures (BACKEND_MISSING, unknown id, ...) are recorded on the
# records, so the error envelope still carries the fresh models/roots lists.
_MODEL_LIFECYCLE_PATHS = ("/api/models/load", "/api/models/unload", "/api/models/remove")

_APPROVE_PATH = re.compile(r"^/api/requests/([^/]+)/approve$")
_DENY_PATH = re.compile(r"^/api/requests/([^/]+)/deny$")
_JOB_DETAIL_PATH = re.compile(r"^/api/models/jobs/([^/]+)$")
_JOB_CANCEL_PATH = re.compile(r"^/api/models/jobs/([^/]+)/cancel$")


class HavenWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address,
        static_root: Path,
        *,
        clock: Clock | None = None,
        models_root: str | Path | None = None,
    ) -> None:
        self.static_root = static_root
        self.director = DemoDirector(clock=clock)
        env_root = os.environ.get(_MODELS_ROOT_ENV)
        if env_root:
            resolved_models_root: str | Path = env_root
        elif models_root is not None:
            resolved_models_root = models_root
        else:
            resolved_models_root = default_models_root()
        # One manager per server: storage/registry are file-based, but the
        # in-memory backend registry and loaded handles are shared state, so
        # every handler thread must talk to this single instance. The job
        # manager shares it: it is thread-safe by design (job map behind a
        # lock, callbacks fired outside it).
        self.models = ModelManager(resolved_models_root)
        self.model_jobs = DownloadJobManager(self.models)
        super().__init__(server_address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = "HavenWeb/0.1"

    def log_message(self, format: str, *args) -> None:
        pass

    @property
    def director(self) -> DemoDirector:
        return self.server.director

    @property
    def models(self) -> ModelManager:
        return self.server.models

    @property
    def model_jobs(self) -> DownloadJobManager:
        return self.server.model_jobs

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._send_json(200, self.director.state())
        elif path == "/api/models":
            self._send_json(200, overview_payload(self.models))
        elif path == "/api/models/jobs":
            self._send_json(200, {"ok": True, "jobs": [job_to_dict(job) for job in self.model_jobs.list()]})
        else:
            match = _JOB_DETAIL_PATH.match(path)
            if match:
                self._send_job_detail(match.group(1))
            elif path == "/api/models/events":
                self._stream_model_events()
            elif path == "/events":
                self._stream_events()
            else:
                self._serve_static(path)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/chat":
            body = self._read_json()
            if body is None:
                return
            state = self.director.chat(str(body.get("text", "")), body.get("focus"))
            self._send_json(200, {"ok": True, "state": state})
            return
        match = _APPROVE_PATH.match(path)
        if match:
            body = self._read_json(optional=True)
            if body is None:
                return
            result = self.director.approve(match.group(1), auto=bool(body.get("auto", False)))
            if result is None:
                self._send_json(404, {"error": "unknown request"})
            else:
                self._send_json(200, {"ok": True, "state": result})
            return
        match = _DENY_PATH.match(path)
        if match:
            if self.director.deny(match.group(1)):
                self._send_json(200, {"ok": True, "state": self.director.state()})
            else:
                self._send_json(404, {"error": "unknown request"})
            return
        if path == "/api/demo/camera-down":
            self._send_json(200, {"ok": True, "state": self.director.mark_camera_down()})
            return
        if path == "/api/demo/camera-up":
            self._send_json(200, {"ok": True, "state": self.director.mark_camera_up()})
            return
        if path == "/api/demo/reset":
            self._send_json(200, {"ok": True, "state": self.director.reset()})
            return
        if path == "/api/voice/wake":
            self._send_json(200, self.director.voice_wake())
            return
        if path == "/api/voice/utterance":
            body = self._read_json()
            if body is None:
                return
            self._send_json(200, self.director.voice_utterance(str(body.get("text", ""))))
            return
        if path == "/api/voice/cancel":
            self._send_json(200, self.director.voice_cancel())
            return
        if path == "/api/models" or path.startswith("/api/models/"):
            self._handle_models_post(path)
            return
        self._send_json(404, {"error": "not found"})

    def _handle_models_post(self, path: str) -> None:
        manager = self.models
        if path == "/api/models/scan":
            self._send_model_result(path, lambda: scan_payload(manager, manager.scan()))
            return
        match = _JOB_CANCEL_PATH.match(path)
        if match:
            self._cancel_job(match.group(1))
            return
        body = self._read_json()
        if body is None:
            return
        if path == "/api/models/download":
            url = self._require_field(body, "url")
            if url is not None:
                self._start_download(url)
            return
        if path == "/api/models/inspect":
            url = self._require_field(body, "url")
            if url is not None:
                self._send_model_result(
                    path, lambda: {"ok": True, "inspection": inspection_payload(manager.inspect_url(url))}
                )
            return
        if path == "/api/models/install-url":
            url = self._require_field(body, "url")
            if url is not None:
                self._send_model_result(path, lambda: self._install(manager.install_from_url(url)))
            return
        if path == "/api/models/install-local":
            folder = self._require_field(body, "folder")
            if folder is not None:
                self._send_model_result(path, lambda: self._install(manager.install_local_folder(folder)))
            return
        if path == "/api/models/add-endpoint":
            url = self._require_field(body, "url")
            if url is not None:
                self._send_model_result(path, lambda: self._install(manager.register_endpoint(url)))
            return
        if path == "/api/models/add-root":
            root = self._require_field(body, "path")
            if root is not None:
                self._send_model_result(path, lambda: self._install(manager.add_root(root)))
            return
        if path == "/api/models/register":
            candidate = self._require_field(body, "path")
            if candidate is not None:
                self._send_model_result(
                    path, lambda: self._install(manager.register_candidate(inspect_folder(candidate)))
                )
            return
        if path in _MODEL_LIFECYCLE_PATHS:
            model_id = self._require_field(body, "id")
            if model_id is None:
                return
            if path == "/api/models/load":
                action = manager.load
            elif path == "/api/models/unload":
                action = manager.unload
            else:
                action = manager.remove
            self._send_model_result(path, lambda: self._install(action(model_id)))
            return
        self._send_json(404, {"error": "not found"})

    def _install(self, record) -> dict:
        # Successful mutations all answer with the fresh models+roots payload;
        # the record itself is persisted state, the lists are the refetch.
        return models_payload(self.models)

    def _require_field(self, body: dict, name: str) -> str | None:
        value = body.get(name)
        if not isinstance(value, str) or not value.strip():
            self._send_json(200, {"ok": False, "error": f"a non-empty '{name}' is required"})
            return None
        return value

    def _send_model_result(self, path: str, action) -> None:
        try:
            self._send_json(200, action())
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
            if path in _MODEL_LIFECYCLE_PATHS:
                payload["models"] = models_payload(self.models)["models"]
                payload["roots"] = models_payload(self.models)["roots"]
            self._send_json(200, payload)

    def _read_json(self, *, optional: bool = False) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw.strip():
            if optional:
                return {}
            self._send_json(400, {"error": "a JSON body is required"})
            return None
        try:
            body = json.loads(raw)
        except ValueError:
            self._send_json(400, {"error": "invalid JSON body"})
            return None
        if not isinstance(body, dict):
            self._send_json(400, {"error": "invalid JSON body"})
            return None
        return body

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        relative = posixpath.normpath(path).lstrip("/")
        if not relative or ".." in relative.split("/"):
            self._send_json(404, {"error": "not found"})
            return
        target = self.server.static_root / relative
        if not target.is_file():
            self._send_json(404, {"error": "not found"})
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_job_detail(self, job_id: str) -> None:
        try:
            job = self.model_jobs.status(job_id)
        except KeyError:
            self._send_json(200, {"ok": False, "error": f"unknown job: {job_id}"})
            return
        self._send_json(200, {"ok": True, "job": job_to_dict(job)})

    def _start_download(self, url: str) -> None:
        job_id = self.model_jobs.start(url)
        job = self.model_jobs.status(job_id)
        if job.state.value == "failed":
            # Resolution failed synchronously: the envelope carries the error
            # and no job id; the failed job itself still shows up in the jobs
            # list so the UI renders it uniformly.
            self._send_json(200, {"ok": False, "error": job.error or "download failed"})
            return
        self._send_json(200, {"ok": True, "job_id": job_id})

    def _cancel_job(self, job_id: str) -> None:
        if not self.model_jobs.cancel(job_id):
            self._send_json(200, {"ok": False, "error": f"unknown job: {job_id}"})
            return
        self._send_json(200, {"ok": True, "job": job_to_dict(self.model_jobs.status(job_id))})

    def _stream_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.close_connection = False
        subscriber = self.director.subscribe()

        def emit(event: str, payload: dict) -> None:
            data = json.dumps(payload)
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
            for line in data.splitlines() or [""]:
                self.wfile.write(f"data: {line}\n".encode("utf-8"))
            self.wfile.write(b"\n")
            self.wfile.flush()

        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            emit("state", self.director.state())
            while True:
                try:
                    kind, payload = subscriber.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": hb\n\n")
                    self.wfile.flush()
                    continue
                if kind == "glow":
                    emit("glow", payload)
                else:
                    emit("state", payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            # The stream is over once this generator exits; without this the
            # handler loops back into readline() on an aborted connection.
            self.close_connection = True
            self.director.unsubscribe(subscriber)

    def _stream_model_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.close_connection = False
        events: queue.Queue = queue.Queue()
        subscriber = events.put
        self.model_jobs.subscribe(subscriber)

        def emit(event: str, payload) -> None:
            data = json.dumps(payload)
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
            for line in data.splitlines() or [""]:
                self.wfile.write(f"data: {line}\n".encode("utf-8"))
            self.wfile.write(b"\n")
            self.wfile.flush()

        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            emit("jobs", [job_to_dict(job) for job in self.model_jobs.list()])
            while True:
                try:
                    job = events.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": hb\n\n")
                    self.wfile.flush()
                    continue
                emit("model_job", job_to_dict(job))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            # Same discipline as /events: stop the handler from looping back
            # into readline() on an aborted connection, then detach.
            self.close_connection = True
            self.model_jobs.unsubscribe(subscriber)


def make_server(
    port: int,
    *,
    clock: Clock | None = None,
    static_root: str | Path | None = None,
    models_root: str | Path | None = None,
) -> tuple[HavenWebServer, DemoDirector]:
    root = Path(static_root) if static_root is not None else Path(__file__).parent / "static"
    server = HavenWebServer(("127.0.0.1", port), root, clock=clock, models_root=models_root)
    return server, server.director


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the HAVEN local web surface.")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    server, _ = make_server(args.port)
    host, port = server.server_address
    print(f"HAVEN web surface listening on http://{host}:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()


__all__ = ["HavenWebServer", "make_server", "main"]
