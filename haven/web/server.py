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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..models import ModelManager, inspect_folder
from ..models.jobs import DownloadJobManager, job_to_dict
from ..models.storage import default_models_root
from .application import build_application
from .haven_application import Clock, HavenApplication
from .diagnostics import BackupManager, SystemDiagnostics
from .models_api import (
    assign_payload,
    inspection_payload,
    models_payload,
    overview_payload,
    scan_payload,
)
from .receipts_api import action_chain, event_action_id
from .service_manager import ServiceManager
from .setup_config import SetupConfigStore, default_data_dir
from .setup_service import SetupService
from .computer_actions import ComputerActionService
from ..actions import ActionLedgerStore
from ..ontology import OntologyStore
from ..resources import ResourceStore
from ..search import HavenSearchService, SearchQuery

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

# Pinned static content types (mimetypes is platform-dependent).
_STATIC_CONTENT_TYPES = {
    ".webmanifest": "application/manifest+json",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
}


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
        demo: bool = False,
        ha_client=None,
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
        # First-run onboarding state lives in `<data_dir>/haven.json`, next to
        # the enrolled-devices sidecar. Construction stays lazy: the data dir
        # is created by the setup steps, not by booting the server, and a
        # broken config surfaces through the endpoints instead of failing here.
        self.setup_store = SetupConfigStore(Path(resolved_data_dir) / "haven.json")
        # Kept so `rebuild_director` can re-run the same composition with the
        # freshly saved setup config; `demo` and `ha_client` are constructor
        # overrides that must survive a rebuild unchanged (an operator who
        # started with --demo stays in demo even if setup writes a provider).
        self._director_clock = clock
        self._director_demo = demo
        self._director_ha_client = ha_client
        # One manager per server: storage/registry are file-based, but the
        # in-memory backend registry and loaded handles are shared state, so
        # every handler thread must talk to this single instance. The job
        # manager shares it: it is thread-safe by design (job map behind a
        # lock, callbacks fired outside it).
        self.models = ModelManager(resolved_models_root)
        self.model_jobs = DownloadJobManager(self.models)
        # The application factory reads the saved setup and builds the user's
        # house when a provider is configured, the demo household otherwise.
        # The director's model bridge routes chat/asr/tts through this same
        # manager, so a model loaded in the UI is a model the demo can speak
        # with.
        self.director = self._build_director()
        # The "life search bar" substrate: independent of the household loop
        # above, the same way `self.models`/`self.backups` are their own
        # subsystems rather than something the governed home loop owns.
        # Built before `self.setup` so the setup service can persist a real
        # computer-provider scan into it.
        self.resources = ResourceStore(Path(resolved_data_dir) / "resources.db")
        self.ontology = OntologyStore(Path(resolved_data_dir) / "ontology.db")
        self.search = HavenSearchService(resources=self.resources, ontology=self.ontology)
        self.action_ledger = ActionLedgerStore(Path(resolved_data_dir) / "action_ledger.db")
        self.setup = SetupService(
            store=self.setup_store,
            director=self.director,
            clock=clock,
            on_rebuild=self.rebuild_director,
            include_demo_candidates=self._director_demo,
            resource_store=self.resources,
        )
        # Authorization + consequence verification in front of
        # `FilesystemProvider.execute()` -- independent of `self.setup`
        # (which only owns the wizard's own enable/roots/scan config), the
        # same way `self.search` sits beside rather than inside it.
        self.computer_actions = ComputerActionService(
            store=self.setup_store,
            director=self.director,
            resource_store=self.resources,
            ledger=self.action_ledger,
            clock=clock,
        )
        # Diagnostics reads through the server itself; backups own the
        # `backups/` subtree of the same single-root data dir.
        self._started_monotonic = time.monotonic()
        self.diagnostics = SystemDiagnostics(server=self)
        self.backups = BackupManager(data_dir=Path(resolved_data_dir))
        # Logon-startup management: the launch command is built lazily per
        # call, so the port getter reads the bound port (ephemeral in tests,
        # fixed in production) at call time, never at construction.
        self.service = ServiceManager(
            data_dir=Path(resolved_data_dir),
            port_getter=lambda: self.server_address[1],
        )
        # Real-mode boot: let the setup discovery scan list live Home
        # Assistant entities. Demo directors carry no source (None) — the
        # scan then lists only local demo candidates.
        if getattr(self.director, "ha_states_source", None) is not None:
            self.setup.attach_ha_states_source(self.director.ha_states_source)
        super().__init__(server_address, _Handler)
        # The demo schedules for real: a daemon tick every 20 s asks the
        # runtime which approved rules are due. Stopped in server_close.
        self.director.start_scheduler()
        # A real always-on voice loop, when native audio and a wake+ASR
        # model pair are available; a no-op (returns False) otherwise, so
        # boot never fails or blocks on missing hardware/models.
        self.director.start_voice()

    def _build_director(self) -> HavenApplication:
        # The application factory reads the saved setup and builds the user's
        # house when a provider is configured, the demo household otherwise.
        # The director's model bridge routes chat/asr/tts through this same
        # manager, so a model loaded in the UI is a model the demo can speak
        # with.
        return build_application(
            store=self.setup_store,
            model_manager=self.models,
            clock=self._director_clock,
            demo=self._director_demo,
            ha_client=self._director_ha_client,
        )

    def rebuild_director(self) -> None:
        """Re-run composition from the just-saved setup config, live.

        Without this, the setup wizard could persist a valid Home Assistant
        connection to disk while the running server kept serving the demo
        household it built at process start -- a resident would see their
        real house only after manually restarting HAVEN. `SetupService`
        calls this immediately after a step changes provider connection, so
        the swap happens in place instead.

        Existing `/events` subscribers are not migrated: `_stream_events`
        notices `self.server.director` no longer matches the director it
        subscribed to and closes the connection, and the browser's
        `EventSource` reconnects automatically, subscribing to the live
        director on its next request.
        """

        old = self.director
        # Stopped and flushed before the new director builds: `_build_director`
        # opens its own connection to the same history.db and loads from it
        # immediately, so old's history must be fully durable and its
        # connection closed first, not just eventually. The old real
        # microphone/speaker (if any) must also release the device before
        # the new director tries to open its own.
        old.stop_scheduler()
        old.stop_voice()
        old.close_history()
        new = self._build_director()
        self.director = new
        self.setup.set_director(new)
        # Rebuilt from the *current* data dir root (`self.setup_store.path
        # .parent` -- already updated if this rebuild followed a
        # `choose_data_dir` move) every time, not just on a data-dir move:
        # these are cheap to construct (schema-ensure only; every real
        # operation opens its own connection per call, same as
        # `HistoryStore`) and are not part of `_build_director()`'s own
        # rebuild, so nothing else keeps them pointed at the right file.
        data_dir = self.setup_store.path.parent
        self.resources = ResourceStore(data_dir / "resources.db")
        self.ontology = OntologyStore(data_dir / "ontology.db")
        self.search = HavenSearchService(resources=self.resources, ontology=self.ontology)
        self.action_ledger = ActionLedgerStore(data_dir / "action_ledger.db")
        self.setup.set_resource_store(self.resources)
        self.computer_actions.set_director(new)
        self.computer_actions.set_resource_store(self.resources)
        self.computer_actions.set_ledger(self.action_ledger)
        # Mirrors the same wiring `__init__` does for the first director:
        # discovery scans must list the new world's real HA entities, not
        # the one this composition replaced.
        self.setup.attach_ha_states_source(getattr(new, "ha_states_source", None))
        new.start_scheduler()
        new.start_voice()

    def server_close(self) -> None:
        self.director.stop_scheduler()
        self.director.stop_voice()
        self.director.close_history()
        super().server_close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "HavenWeb/0.1"

    def log_message(self, format: str, *args) -> None:
        pass

    @property
    def director(self) -> HavenApplication:
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

    @property
    def diagnostics(self) -> SystemDiagnostics:
        return self.server.diagnostics

    @property
    def backups(self) -> BackupManager:
        return self.server.backups

    @property
    def service(self) -> ServiceManager:
        return self.server.service

    @property
    def search(self) -> HavenSearchService:
        return self.server.search

    @property
    def computer_actions(self) -> ComputerActionService:
        return self.server.computer_actions

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
        elif path == "/api/setup/providers/packages":
            self._send_json(200, self.setup_service.list_provider_packages())
        elif path == "/api/system/diagnostics":
            self._send_json(200, self.diagnostics.collect())
        elif path == "/api/system/backups":
            self._send_json(200, {"ok": True, "backups": self.backups.list()["backups"]})
        elif path == "/api/system/service":
            self._send_json(200, {"ok": True, "service": self.service.status()})
        elif path == "/api/search":
            self._send_search(parse_qs(urlsplit(self.path).query))
        elif path == "/api/computer/actions/history":
            self._send_json(200, self.computer_actions.history())
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
        if path == "/api/computer/actions" or path.startswith("/api/computer/actions/"):
            self._handle_computer_action_post(path)
            return
        if path == "/api/system/diagnostics/probe":
            result = self.diagnostics.probe_provider()
            self._send_json(200 if result.get("ok") else 400, result)
            return
        if path in ("/api/system/service/install", "/api/system/service/uninstall"):
            action = self.service.install if path.endswith("/install") else self.service.uninstall
            result = action()
            self._send_json(200 if result.get("ok") else 400, result)
            return
        if path == "/api/system/backup":
            self._send_json(200, {"ok": True, "backup": self.backups.create()})
            return
        if path in ("/api/system/backup/restore", "/api/system/backup/delete"):
            body = self._read_json()
            if body is None:
                return
            backup_id = body.get("id")
            if not isinstance(backup_id, str) or not backup_id.strip():
                self._send_json(400, {"ok": False, "error": "a non-empty 'id' is required"})
                return
            action = self.backups.restore if path.endswith("/restore") else self.backups.delete
            try:
                result = action(backup_id.strip())
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {"ok": True, "result": result})
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
        if path == "/api/setup/computer/scan":
            self._send_setup_result(setup.scan_computer_provider())
            return
        body = self._read_json(optional=True)
        if body is None:
            return
        if path == "/api/setup/data-dir":
            value = body.get("path")
            result = setup.choose_data_dir(value if isinstance(value, str) else None)
        elif path == "/api/setup/providers/install":
            result = setup.install_provider_package(
                entry_point_name=body.get("entry_point_name"), config=body.get("config")
            )
        elif path == "/api/setup/providers/enable":
            result = setup.set_provider_package_enabled(
                provider_id=body.get("provider_id"), enabled=bool(body.get("enabled", True))
            )
        elif path == "/api/setup/providers/uninstall":
            result = setup.uninstall_provider_package(provider_id=body.get("provider_id"))
        elif path == "/api/setup/computer":
            read_only = body.get("read_only")
            result = setup.set_computer_provider_enabled(
                enabled=bool(body.get("enabled", False)),
                read_only=bool(read_only) if isinstance(read_only, bool) else None,
            )
        elif path == "/api/setup/computer/roots":
            result = setup.add_computer_provider_root(path=body.get("path"))
        elif path == "/api/setup/computer/roots/remove":
            result = setup.remove_computer_provider_root(path=body.get("path"))
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
        elif path == "/api/setup/household/people":
            role = body.get("role")
            result = setup.declare_person(
                name=body.get("name"),
                entity_id=body.get("entity_id"),
                room_id=body.get("room_id"),
                role=role if isinstance(role, str) and role.strip() else "member",
            )
        elif path == "/api/setup/household/people/remove":
            result = setup.remove_person(person_id=body.get("person_id"))
        elif path == "/api/setup/household/contexts":
            result = setup.declare_context(label=body.get("label"), entity_id=body.get("entity_id"))
        elif path == "/api/setup/household/contexts/remove":
            result = setup.remove_context(context_id=body.get("context_id"))
        else:
            self._send_json(404, {"error": "not found"})
            return
        self._send_setup_result(result)

    def _send_setup_result(self, result: dict) -> None:
        if result.get("ok"):
            self._send_json(200, result)
        else:
            self._send_json(400, result)

    def _handle_computer_action_post(self, path: str) -> None:
        actions = self.computer_actions
        body = self._read_json()
        if body is None:
            return
        if path == "/api/computer/actions":
            parameters = body.get("parameters")
            result = actions.request_action(
                action=body.get("action"),
                resource_id=body.get("resource_id"),
                parameters=parameters if isinstance(parameters, dict) else None,
                justification=body.get("justification"),
            )
        elif path == "/api/computer/actions/confirm":
            result = actions.confirm_action(request_id=body.get("request_id"))
        elif path == "/api/computer/actions/deny":
            result = actions.deny_action(request_id=body.get("request_id"))
        else:
            self._send_json(404, {"error": "not found"})
            return
        self._send_setup_result(result)

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
        # mimetypes is platform-dependent (Windows reads the registry), so
        # pin the types the PWA shell relies on instead of guessing.
        content_type = _STATIC_CONTENT_TYPES.get(
            target.suffix.lower(),
            mimetypes.guess_type(str(target))[0] or "application/octet-stream",
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        # Service-worker update checks require a fresh script: the browser
        # must revalidate /sw.js on every navigation, never serve it heuristically.
        if relative == "sw.js":
            self.send_header("Cache-Control", "no-cache")
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
        # Command recording is a simulated-adapter affordance; live adapters
        # (Home Assistant REST) have no local command log to read.
        executed_commands = director.adapter.commands if director.adapter is not None else ()

        def _service_for_kind(action_kind):
            # The runtime's static routing table; "haven.unmapped" means the
            # kind has no service and should read as unavailable here.
            service = director.runtime._service_for(action_kind)
            return None if service == "haven.unmapped" else service

        chain = action_chain(
            action_id,
            store=director.store,
            receipts=director.receipts,
            device_registry=director.engine.device_registry,
            executed_commands=executed_commands,
            service_for_kind=_service_for_kind,
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

    def _send_search(self, params: dict) -> None:
        text = params.get("q", [""])[0].strip()
        if not text:
            self._send_json(200, {"ok": False, "error": "a non-empty 'q' query parameter is required"})
            return
        scope_ids = tuple(v for v in params.get("scope", []) if v)
        resource_types = tuple(v for v in params.get("type", []) if v)
        try:
            limit = int(params.get("limit", ["20"])[0])
        except ValueError:
            limit = 20
        if limit <= 0:
            limit = 20
        include_stale = params.get("include_stale", ["false"])[0].strip().lower() in ("1", "true", "yes")
        query = SearchQuery(
            text=text,
            scope_ids=scope_ids,
            resource_types=resource_types,
            limit=limit,
            include_stale=include_stale,
        )
        hits = self.search.search(query)
        self._send_json(
            200,
            {
                "ok": True,
                "hits": [
                    {
                        "resource_id": hit.resource_id,
                        "score": hit.score,
                        "reason": hit.reason,
                        "matched_refs": list(hit.matched_refs),
                    }
                    for hit in hits
                ],
            },
        )

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
        # Captured once: if the composition root rebuilds the household
        # mid-stream (setup just connected a provider), this subscriber
        # queue belongs to the director being retired and will never
        # receive another event.
        director = self.director
        subscriber = director.subscribe()

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
            emit("state", director.state())
            while True:
                if self.server.director is not director:
                    # `rebuild_director` swapped in a new household: end this
                    # connection so the browser's EventSource reconnects and
                    # subscribes to the live director instead of one that has
                    # stopped receiving events.
                    break
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
            director.unsubscribe(subscriber)

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
    demo: bool = False,
    ha_client=None,
) -> tuple[HavenWebServer, HavenApplication]:
    root = Path(static_root) if static_root is not None else Path(__file__).parent / "static"
    server = HavenWebServer(
        ("127.0.0.1", port), root, clock=clock, models_root=models_root, data_dir=data_dir,
        demo=demo, ha_client=ha_client,
    )
    return server, server.director


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the HAVEN local web surface.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="force the demo household; normal boot reads the saved setup and builds "
        "the user's house when a provider is configured",
    )
    args = parser.parse_args(argv)
    server, _ = make_server(args.port, demo=args.demo)
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
