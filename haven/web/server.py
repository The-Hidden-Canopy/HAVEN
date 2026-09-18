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
from urllib.parse import parse_qs, urlsplit

from ..models import ModelManager, inspect_folder
from ..models.jobs import DownloadJobManager, job_to_dict
from ..models.storage import default_models_root
from .demo import Clock, DemoDirector
from .models_api import (
    assign_payload,
    inspection_payload,
    models_payload,
    overview_payload,
    scan_payload,
)
from .receipts_api import action_chain, event_action_id
from .setup_config import SetupConfigStore, default_data_dir
from .setup_service import SetupService

HEARTBEAT_SECONDS = 15

_MODELS_ROOT_ENV = "HAVEN_MODELS_ROOT"
_DATA_DIR_ENV = "HAVEN_DATA_DIR"
# Lifecycle failures (BACKEND_MISSING, unknown id, ...) are recorded on the
# records, so the error envelope still carries the fresh models/roots lists.
_MODEL_LIFECYCLE_PATHS = ("/api/models/load", "/api/models/unload", "/api/models/remove")

_APPROVE_PATH = re.compile(r"^/api/requests/([^/]+)/approve$")
_DENY_PATH = re.compile(r"^/api/requests/([^/]+)/deny$")
_DEVICE_COMMAND_PATH = re.compile(r"^/api/devices/([^/]+)/command$")
_SCHEDULER_ENABLED_PATH = re.compile(r"^/api/scheduler/rules/([^/]+)/enabled$")
_JOB_DETAIL_PATH = re.compile(r"^/api/models/jobs/([^/]+)$")
_JOB_CANCEL_PATH = re.compile(r"^/api/models/jobs/([^/]+)/cancel$")
_CHAIN_PATH = re.compile(r"^/api/actions/([^/]+)/chain$")


class HavenWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address,
        static_root: Path,
        *,
        clock: Clock | None = None,
        models_root: str | Path | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self.static_root = static_root
        env_root = os.environ.get(_MODELS_ROOT_ENV)
        if env_root:
            resolved_models_root: str | Path = env_root
        elif models_root is not None:
            resolved_models_root = models_root
        else:
            resolved_models_root = default_models_root()
        env_data_dir = os.environ.get(_DATA_DIR_ENV)
        if env_data_dir:
            resolved_data_dir: str | Path = env_data_dir
        elif data_dir is not None:
            resolved_data_dir = data_dir
        else:
            resolved_data_dir = default_data_dir()
        # One manager per server: storage/registry are file-based, but the
        # in-memory backend registry and loaded handles are shared state, so
        # every handler thread must talk to this single instance. The job
        # manager shares it: it is thread-safe by design (job map behind a
        # lock, callbacks fired outside it).
        self.models = ModelManager(resolved_models_root)
        self.model_jobs = DownloadJobManager(self.models)
        # The director's model bridge routes chat/asr/tts through this same
        # manager, so a model loaded in the UI is a model the demo can speak
        # with.
        self.director = DemoDirector(clock=clock, model_manager=self.models)
        # First-run onboarding state lives in `<data_dir>/haven.json`, next to
        # the enrolled-devices sidecar. Construction stays lazy: the data dir
        # is created by the setup steps, not by booting the server, and a
        # broken config surfaces through the endpoints instead of failing here.
        self.setup_store = SetupConfigStore(Path(resolved_data_dir) / "haven.json")
        self.setup = SetupService(store=self.setup_store, director=self.director, clock=clock)
        super().__init__(server_address, _Handler)
        # The demo schedules for real: a daemon tick every 20 s asks the
        # runtime which approved rules are due. Stopped in server_close.
        self.director.start_scheduler()

    def server_close(self) -> None:
        self.director.stop_scheduler()
        super().server_close()


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

    @property
    def setup_service(self) -> SetupService:
        return self.server.setup

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._send_json(200, self.director.state())
        elif path == "/api/scheduler":
            self._send_json(200, {"ok": True, "scheduler": self.director.scheduler_status()})
        elif path == "/api/models":
            self._send_json(200, overview_payload(self.models))
        elif path == "/api/models/jobs":
            self._send_json(200, {"ok": True, "jobs": [job_to_dict(job) for job in self.model_jobs.list()]})
        elif path == "/api/setup":
            self._send_json(200, self.setup_service.status())
        else:
            match = _JOB_DETAIL_PATH.match(path)
            if match:
                self._send_job_detail(match.group(1))
            elif path == "/api/models/events":
                self._stream_model_events()
            elif path == "/events":
                self._stream_events()
            elif path == "/api/actions/chain":
                self._send_event_chain(parse_qs(urlsplit(self.path).query).get("event_id", [""])[0])
            else:
                match = _CHAIN_PATH.match(path)
                if match:
                    self._send_action_chain(match.group(1))
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
        match = _DEVICE_COMMAND_PATH.match(path)
        if match:
            body = self._read_json()
            if body is None:
                return
            service = body.get("service")
            if not isinstance(service, str) or not service.strip():
                self._send_json(400, {"ok": False, "error": "a non-empty 'service' is required"})
                return
            parameters = None
            if service == "light.set_brightness":
                brightness = body.get("brightness_pct")
                if isinstance(brightness, bool) or not isinstance(brightness, int):
                    self._send_json(400, {"ok": False, "error": "an integer 'brightness_pct' is required"})
                    return
                parameters = {"brightness_pct": brightness}
            result = self.director.device_command(match.group(1), service, parameters)
            if not result.get("ok"):
                self._send_json(400, result)
            else:
                self._send_json(200, result)
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
        if path == "/api/scheduler/tick":
            self.director.run_scheduler_tick()
            self._send_json(200, {"ok": True, "scheduler": self.director.scheduler_status()})
            return
        match = _SCHEDULER_ENABLED_PATH.match(path)
        if match:
            body = self._read_json()
            if body is None:
                return
            rule_id = match.group(1)
            if not self.director.has_rule(rule_id):
                self._send_json(404, {"error": "unknown rule"})
                return
            self._send_json(
                200,
                {"ok": True, "scheduler": self.director.set_scheduler_enabled(rule_id, bool(body.get("enabled")))},
            )
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
        if path == "/api/models/assign":
            body = self._read_json()
            if body is None:
                return
            role = self._require_field(body, "role")
            if role is None:
                return
            self._send_json(200, assign_payload(self.models, role, body.get("id")))
            return
        if path == "/api/models" or path.startswith("/api/models/"):
            self._handle_models_post(path)
            return
        if path == "/api/setup" or path.startswith("/api/setup/"):
            self._handle_setup_post(path)
            return
        self._send_json(404, {"error": "not found"})

    def _handle_setup_post(self, path: str) -> None:
        setup = self.server.setup
        if path == "/api/setup/discovery/scan":
            self._send_json(200, setup.run_discovery())
            return
        if path == "/api/setup/complete":
            self._send_json(200, setup.complete())
            return
        if path == "/api/setup/reopen":
            self._send_json(200, setup.reopen())
            return
        body = self._read_json(optional=True)
        if body is None:
            return
        if path == "/api/setup/data-dir":
            value = body.get("path")
            result = setup.choose_data_dir(value if isinstance(value, str) else None)
        elif path == "/api/setup/provider":
            kind = body.get("kind")
            base_url = body.get("base_url")
            token = body.get("token")
            result = setup.connect_provider(
                kind=kind if isinstance(kind, str) else None,
                base_url=base_url if isinstance(base_url, str) else None,
                token=token if isinstance(token, str) else None,
                skip=bool(body.get("skip", False)),
            )
        elif path == "/api/setup/enroll":
            candidate_id = body.get("candidate_id")
            device_type = body.get("device_type")
            room = body.get("room")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                self._send_json(400, {"ok": False, "error": "a non-empty 'candidate_id' is required"})
                return
            if not isinstance(device_type, str) or not device_type.strip():
                self._send_json(400, {"ok": False, "error": "a non-empty 'device_type' is required"})
                return
            result = setup.enroll(
                candidate_id.strip(),
                device_type=device_type.strip(),
                room=room if isinstance(room, str) and room.strip() else None,
            )
        elif path == "/api/setup/preferences":
            result = setup.set_preferences(voice=body.get("voice"), intelligence=body.get("intelligence"))
        else:
            self._send_json(404, {"error": "not found"})
            return
        self._send_setup_result(result)

    def _send_setup_result(self, result: dict) -> None:
        if result.get("ok"):
            self._send_json(200, result)
        else:
            self._send_json(400, result)

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

    def _send_action_chain(self, action_id: str) -> None:
        director = self.director
        chain = action_chain(
            action_id,
            store=director.store,
            receipts=director.receipts,
            device_registry=director.engine.device_registry,
            executed_commands=director.adapter.commands,
        )
        if chain is None:
            self._send_json(200, {"ok": False, "error": f"unknown action: {action_id}"})
            return
        self._send_json(200, {"ok": True, "chain": chain})

    def _send_event_chain(self, event_id: str) -> None:
        # Activity rows expose event ids but not action ids or correlation
        # ids, so the drill-down resolves the row's event to its action.
        if not event_id:
            self._send_json(200, {"ok": False, "error": "an event_id query parameter is required"})
            return
        action_id = event_action_id(self.director.store, event_id)
        if action_id is None:
            self._send_json(200, {"ok": False, "error": f"unknown or non-action event: {event_id}"})
            return
        self._send_action_chain(action_id)

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
    data_dir: str | Path | None = None,
) -> tuple[HavenWebServer, DemoDirector]:
    root = Path(static_root) if static_root is not None else Path(__file__).parent / "static"
    server = HavenWebServer(
        ("127.0.0.1", port), root, clock=clock, models_root=models_root, data_dir=data_dir
    )
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
