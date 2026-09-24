"""Stdlib HTTP surface for the HAVEN demo: JSON API, SSE stream, static files."""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import posixpath
import queue
import re
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, unquote, urlsplit

from ..models import ModelManager, inspect_folder
from ..ipc import IpcDispatcher
from ..models.jobs import DownloadJobManager, job_to_dict
from ..models.storage import default_models_root
from .application import build_application
from ..intelligence.intents import MutationProposal
from .haven_application import Clock, HavenApplication
from .authoring_intent import parse_authoring_intent
from .diagnostics import BackupManager, SystemDiagnostics
from .models_api import (
    assign_payload,
    inspection_payload,
    models_payload,
    overview_payload,
    scan_payload,
)
from ..plugins import PluginManager, PluginRegistry
from .plugins_api import (
    current_payload as plugins_current_payload,
    marketplace_payload as plugins_marketplace_payload,
    refresh_payload as plugins_refresh_payload,
)
from .receipts_api import action_chain, event_action_id
from .service_manager import StartupManager
from .folder_picker import choose_folder
from .setup_config import SetupConfigStore, default_data_dir
from .setup_service import SetupService
from .computer_actions import ComputerActionService
from ..actions import ActionLedgerStore
from ..knowledge import ClaimStore, KnowledgeService
from ..knowledge.audit import audit_event_to_dict
from ..knowledge.claims import ClaimState
from ..knowledge.store import claim_fingerprint, claim_to_dict
from ..ontology import OntologyStore
from ..resources import ResourceStore
from ..resources.store import resource_record_to_dict
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
_PLUGIN_ENABLE_PATH = re.compile(r"^/api/plugins/([^/]+)/enable$")
_PLUGIN_DISABLE_PATH = re.compile(r"^/api/plugins/([^/]+)/disable$")
_CHAIN_PATH = re.compile(r"^/api/actions/([^/]+)/chain$")
_KNOWLEDGE_CLAIM_PATH = re.compile(r"^/api/knowledge/claims/([^/]+)$")
_KNOWLEDGE_CLAIM_ACTION_PATH = re.compile(r"^/api/knowledge/claims/([^/]+)/(correct|stale|forget)$")
_AUTHORING_AUTOMATION_ACTION_PATH = re.compile(r"^/api/automations/([^/]+)/(approve|revoke)$")

# Pinned static content types (mimetypes is platform-dependent).
_STATIC_CONTENT_TYPES = {
    ".webmanifest": "application/manifest+json",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".png": "image/png",
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
        session_token: str | None = None,
        bootstrap_token: str | None = None,
        activation_token: str | None = None,
        on_activate=None,
        folder_picker=None,
        before_data_dir_changed=None,
        on_data_dir_changed=None,
        on_data_dir_change_failed=None,
    ) -> None:
        self.static_root = static_root
        self._session_token = session_token
        # The bootstrap token travels in the Edge command line, while the
        # session token is only ever accepted as an HttpOnly cookie.  Keep a
        # compatibility fallback for direct callers that still provide the
        # old single token, but the desktop shell always supplies both.
        self._bootstrap_token = session_token if bootstrap_token is None else bootstrap_token
        self._bootstrap_lock = Lock()
        self._activation_token = activation_token
        self._activation_handler = on_activate
        self.folder_picker = folder_picker
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
        # The plugin marketplace is a separate concept from a model: it never
        # runs in-process and never receives live household state (see
        # docs/plugin-boundary.md). Enablement is local, file-backed state,
        # same pattern as the model registry; the catalog itself is fetched
        # from the Hub on demand, never at boot, so a network hiccup can
        # never block startup.
        self.plugins = PluginManager(PluginRegistry(Path(resolved_data_dir) / "plugins.json"), clock=clock)
        # The application factory always builds the real installation on a
        # normal boot. Only an explicit --demo constructor flag creates the
        # simulated household; a fresh install is a real, empty HAVEN world.
        # The director's model bridge routes chat/asr/tts through this same
        # manager.
        self.director = self._build_director()
        # The "life search bar" substrate: independent of the household loop
        # above, the same way `self.models`/`self.backups` are their own
        # subsystems rather than something the governed home loop owns.
        # Built before `self.setup` so the setup service can persist a real
        # computer-provider scan into it.
        self.resources = ResourceStore(Path(resolved_data_dir) / "resources.db")
        self.ontology = OntologyStore(Path(resolved_data_dir) / "ontology.db")
        self.claims = ClaimStore(Path(resolved_data_dir) / "claims.db")
        self.knowledge = KnowledgeService(resources=self.resources, claims=self.claims, clock=clock)
        self.search = HavenSearchService(resources=self.resources, ontology=self.ontology, claims=self.claims)
        self.action_ledger = ActionLedgerStore(Path(resolved_data_dir) / "action_ledger.db")
        self.setup = SetupService(
            store=self.setup_store,
            director=self.director,
            clock=clock,
            on_rebuild=self.rebuild_director,
            before_data_dir_changed=before_data_dir_changed,
            on_data_dir_changed=on_data_dir_changed,
            on_data_dir_change_failed=on_data_dir_change_failed,
            include_demo_candidates=self._director_demo,
            resource_store=self.resources,
            knowledge_service=self.knowledge,
        )
        # Authorization + consequence verification in front of
        # `FilesystemProvider.execute_provider()` -- independent of `self.setup`
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
        self.service = StartupManager(
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

    def host_capabilities(self) -> dict:
        """Describe the native host surface available to the renderer.

        The renderer is shared by browser, desktop, and future tablet hosts.
        It must ask what this host can do instead of assuming that a desktop
        endpoint exists just because the same HTML is being served.
        """

        if self.folder_picker is None:
            return {"host": "browser", "capabilities": []}
        return {"host": "desktop", "capabilities": ["folder_picker"]}

    def rotate_bootstrap_token(self, token: str) -> None:
        """Install the one-shot nonce for the next desktop window.

        The resident server outlives individual Edge windows. A new window
        therefore needs a new URL capability after the previous bootstrap was
        consumed; the session cookie remains unchanged.
        """

        if not isinstance(token, str) or not token:
            raise ValueError("desktop bootstrap token must be a non-empty string")
        with self._bootstrap_lock:
            self._bootstrap_token = token

    def build_ipc_dispatcher(self) -> IpcDispatcher:
        """Build the native-client adapter over existing governed services.

        This is intentionally an adapter, not a second application runtime.
        Mutating methods delegate to the same ``SetupService``,
        ``HavenApplication`` and ``ComputerActionService`` instances used by
        the debug web surface.  The native client receives only the current
        local household scope until ``IdentityProvider`` membership policy is
        implemented; arbitrary caller-supplied scopes are rejected here.
        """

        household_id = self.director.household_id

        def _visible_scope_ids(params: dict) -> tuple[str, ...]:
            requested = params.get("scope_ids", ())
            if requested is None:
                requested = ()
            if not isinstance(requested, (list, tuple)) or any(
                not isinstance(scope_id, str) or not scope_id.strip() for scope_id in requested
            ):
                raise ValueError("scope_ids must be a list of non-empty strings")
            requested_ids = tuple(requested)
            if requested_ids and set(requested_ids) != {household_id}:
                raise ValueError("the native client may only query its authenticated household scope")
            return (household_id,)

        def _search(params: dict) -> dict:
            text = params.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("a non-empty 'text' query is required")
            resource_types = params.get("resource_types", ())
            if not isinstance(resource_types, (list, tuple)) or any(
                not isinstance(value, str) or not value.strip() for value in resource_types
            ):
                raise ValueError("resource_types must be a list of non-empty strings")
            limit = params.get("limit", 20)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
                raise ValueError("limit must be an integer between 1 and 200")
            include_stale = params.get("include_stale", False)
            if not isinstance(include_stale, bool):
                raise ValueError("include_stale must be a boolean")
            query = SearchQuery(
                text=text,
                scope_ids=_visible_scope_ids(params),
                resource_types=tuple(resource_types),
                limit=limit,
                include_stale=include_stale,
            )
            hits = self.search.search(query)
            return {
                "hits": [
                    {
                        "resource_id": hit.resource_id,
                        "score": hit.score,
                        "reason": hit.reason,
                        "matched_refs": list(hit.matched_refs),
                        "resource": (
                            resource_record_to_dict(resource)
                            if (resource := self.resources.get(hit.resource_id)) is not None
                            else None
                        ),
                    }
                    for hit in hits
                ]
            }

        def _knowledge_claims(params: dict) -> dict:
            include_stale = params.get("include_stale", False)
            if not isinstance(include_stale, bool):
                raise ValueError("include_stale must be a boolean")
            claims = self.knowledge.list_claims(
                scope_id=_visible_scope_ids(params)[0],
                include_stale=include_stale,
            )
            return {"claims": [claim_to_dict(claim) for claim in claims[:200]]}

        def _knowledge_claim(params: dict) -> dict:
            claim_id = params.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id.strip():
                raise ValueError("a non-empty 'claim_id' is required")
            claim = self.claims.get(claim_id.strip())
            if claim is None or claim.scope_id != _visible_scope_ids(params)[0]:
                # Keep the native surface fail-closed and avoid confirming
                # whether a claim in another scope exists.
                raise ValueError("unknown claim")
            payload = claim_to_dict(claim)
            payload["fingerprint"] = claim_fingerprint(claim)
            payload["sources"] = [
                {
                    "ref": ref,
                    "resource": (
                        resource_record_to_dict(resource)
                        if (resource := self.resources.get(ref)) is not None
                        else None
                    ),
                }
                for ref in claim.source_refs
            ]
            payload["contradictions"] = [
                claim_to_dict(found) for found in self.claims.contradictions_of(claim.claim_id)
            ]
            payload["superseded_claims"] = [
                claim_to_dict(found) for found in self.claims.supersedes_of(claim.claim_id)
            ]
            payload["audit"] = [
                audit_event_to_dict(event)
                for event in self.claims.list_audit(
                    scope_id=claim.scope_id,
                    claim_id=claim.claim_id,
                )
            ]
            return {"claim": payload}

        def _knowledge_owner_actor() -> str:
            if not getattr(self.director, "has_declared_owner", False):
                raise ValueError("a declared owner is required for knowledge changes")
            principal = getattr(self.director, "owner", None)
            actor = getattr(principal, "actor_id", None)
            if not isinstance(actor, str) or not actor.strip() or actor == "no_owner_declared":
                raise ValueError("a declared owner is required for knowledge changes")
            return actor.strip()

        def _knowledge_mutation_claim(params: dict):
            claim_id = params.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id.strip():
                raise ValueError("a non-empty 'claim_id' is required")
            claim = self.claims.get(claim_id.strip())
            if claim is None or claim.scope_id != _visible_scope_ids(params)[0]:
                raise ValueError("unknown claim")
            _knowledge_owner_actor()
            return claim

        def _knowledge_correct(params: dict) -> dict:
            claim = _knowledge_mutation_claim(params)
            proposition = params.get("proposition")
            if not isinstance(proposition, str) or not proposition.strip():
                raise ValueError("a non-empty 'proposition' is required")
            result = self.knowledge.correct_claim(
                claim.claim_id,
                proposition=proposition,
                actor=_knowledge_owner_actor(),
            )
            if result.claim is None:
                raise ValueError(result.reason or "correction rejected")
            return {"claim": claim_to_dict(result.claim)}

        def _knowledge_stale(params: dict) -> dict:
            claim = _knowledge_mutation_claim(params)
            changed = self.knowledge.mark_claim_stale(
                claim.claim_id,
                actor=_knowledge_owner_actor(),
            )
            current = self.claims.get(claim.claim_id)
            return {
                "changed": changed,
                "claim": claim_to_dict(current or claim),
            }

        def _knowledge_forget(params: dict) -> dict:
            claim = _knowledge_mutation_claim(params)
            self.knowledge.forget_claim(claim, forgotten_by=_knowledge_owner_actor())
            current = self.claims.get(claim.claim_id)
            return {"claim": claim_to_dict(current or claim)}

        def _rooms_payload() -> dict:
            # One observation pass feeds both the room list and the pending
            # confirmation pool, so a refresh button can repaint the whole
            # view without a second state snapshot drifting underneath it.
            state = self.director.state()
            return {"rooms": state["rooms"], "pending": state["pending"]}

        def _rooms_get(params: dict) -> dict:
            room_id = params.get("room_id")
            if not isinstance(room_id, str) or not room_id.strip():
                raise ValueError("a non-empty 'room_id' is required")
            payload = _rooms_payload()
            room = next((room for room in payload["rooms"] if room["id"] == room_id.strip()), None)
            if room is None:
                raise ValueError("unknown room")
            return {"room": room, "pending": payload["pending"]}

        def _device_command(params: dict) -> dict:
            # Mirrors the /api/devices/{id}/command handler: the same
            # brightness validation, then the same governed director path.
            # Director-level refusals ride inside the result envelope exactly
            # like computer.action.request, with the web surface's strings.
            device_id = params.get("device_id")
            if not isinstance(device_id, str) or not device_id.strip():
                raise ValueError("a non-empty 'device_id' is required")
            service = params.get("service")
            if not isinstance(service, str) or not service.strip():
                raise ValueError("a non-empty 'service' is required")
            parameters = None
            if service == "light.set_brightness":
                brightness = params.get("brightness_pct")
                if isinstance(brightness, bool) or not isinstance(brightness, int):
                    raise ValueError("an integer 'brightness_pct' is required")
                parameters = {"brightness_pct": brightness}
            return self.director.device_command(device_id.strip(), service, parameters)

        def _request_approve(params: dict) -> dict:
            request_id = params.get("request_id")
            if not isinstance(request_id, str) or not request_id.strip():
                raise ValueError("a non-empty 'request_id' is required")
            state = self.director.approve(request_id.strip())
            if state is None:
                raise ValueError("unknown request")
            return {"state": state}

        def _request_deny(params: dict) -> dict:
            request_id = params.get("request_id")
            if not isinstance(request_id, str) or not request_id.strip():
                raise ValueError("a non-empty 'request_id' is required")
            if not self.director.deny(request_id.strip()):
                raise ValueError("unknown request")
            return {"state": self.director.state()}

        def _computer_action(params: dict) -> dict:
            action = params.get("action")
            parameters = params.get("parameters", {})
            if parameters is not None and not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            return self.computer_actions.request_action(
                action=action,
                resource_id=params.get("resource_id"),
                parameters=parameters,
                justification=params.get("justification"),
            )

        def _computer_confirm(params: dict) -> dict:
            return self.computer_actions.confirm_action(request_id=params.get("request_id"))

        def _computer_deny(params: dict) -> dict:
            return self.computer_actions.deny_action(request_id=params.get("request_id"))

        def _ask(params: dict) -> dict:
            text = params.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("a non-empty 'text' request is required")
            focus = params.get("focus")
            if focus is not None and not isinstance(focus, str):
                raise ValueError("focus must be a string or null")
            return self.director.chat(text, focus)

        def _setup_data_dir(params: dict) -> dict:
            value = params.get("path")
            return self.setup.choose_data_dir(value if isinstance(value, str) else None)

        def _setup_provider_connect(params: dict) -> dict:
            kind = params.get("kind")
            base_url = params.get("base_url")
            token = params.get("token")
            return self.setup.connect_provider(
                kind=kind if isinstance(kind, str) else None,
                base_url=base_url if isinstance(base_url, str) else None,
                token=token if isinstance(token, str) else None,
                skip=bool(params.get("skip", False)),
            )

        def _setup_enroll(params: dict) -> dict:
            candidate_id = params.get("candidate_id")
            device_type = params.get("device_type")
            room = params.get("room")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                raise ValueError("a non-empty 'candidate_id' is required")
            if not isinstance(device_type, str) or not device_type.strip():
                raise ValueError("a non-empty 'device_type' is required")
            return self.setup.enroll(
                candidate_id.strip(),
                device_type=device_type.strip(),
                room=room if isinstance(room, str) and room.strip() else None,
            )

        def _setup_household_people_add(params: dict) -> dict:
            role = params.get("role")
            return self.setup.declare_person(
                name=params.get("name"),
                entity_id=params.get("entity_id"),
                room_id=params.get("room_id"),
                role=role if isinstance(role, str) and role.strip() else "member",
            )

        def _setup_household_contexts_add(params: dict) -> dict:
            return self.setup.declare_context(label=params.get("label"), entity_id=params.get("entity_id"))

        def _setup_preferences(params: dict) -> dict:
            return self.setup.set_preferences(voice=params.get("voice"), intelligence=params.get("intelligence"))

        def _setup_computer(params: dict) -> dict:
            read_only = params.get("read_only")
            return self.setup.set_computer_provider_enabled(
                enabled=bool(params.get("enabled", False)),
                read_only=bool(read_only) if isinstance(read_only, bool) else None,
            )

        def _setup_provider_package_install(params: dict) -> dict:
            return self.setup.install_provider_package(
                entry_point_name=params.get("entry_point_name"), config=params.get("config")
            )

        def _setup_provider_package_enable(params: dict) -> dict:
            return self.setup.set_provider_package_enabled(
                provider_id=params.get("provider_id"), enabled=bool(params.get("enabled", True))
            )

        return IpcDispatcher(
            {
                "host.capabilities": lambda _params: {
                    **self.host_capabilities(),
                    "ipc_protocol": "haven-ipc-1",
                    "transport": "windows_named_pipe",
                },
                "state.get": lambda _params: self.director.state(),
                "setup.status": lambda _params: self.setup.status(),
                "setup.data_dir": _setup_data_dir,
                "setup.provider.connect": _setup_provider_connect,
                "setup.enroll": _setup_enroll,
                "setup.discovery.scan": lambda _params: self.setup.run_discovery(),
                "setup.household.people.add": _setup_household_people_add,
                "setup.household.people.remove": lambda params: self.setup.remove_person(
                    person_id=params.get("person_id")
                ),
                "setup.household.contexts.add": _setup_household_contexts_add,
                "setup.household.contexts.remove": lambda params: self.setup.remove_context(
                    context_id=params.get("context_id")
                ),
                "setup.preferences": _setup_preferences,
                "setup.computer": _setup_computer,
                "setup.computer.roots.add": lambda params: self.setup.add_computer_provider_root(
                    path=params.get("path")
                ),
                "setup.computer.roots.remove": lambda params: self.setup.remove_computer_provider_root(
                    path=params.get("path")
                ),
                "setup.computer.scan": lambda _params: self.setup.scan_computer_provider(),
                "setup.complete": lambda _params: self.setup.complete(),
                "setup.reopen": lambda _params: self.setup.reopen(),
                "setup.providers.packages": lambda _params: self.setup.list_provider_packages(),
                "setup.providers.install": _setup_provider_package_install,
                "setup.providers.enable": _setup_provider_package_enable,
                "setup.providers.uninstall": lambda params: self.setup.uninstall_provider_package(
                    provider_id=params.get("provider_id")
                ),
                "models.overview": lambda _params: overview_payload(self.models),
                "search.query": _search,
                "knowledge.claims": _knowledge_claims,
                "knowledge.claim": _knowledge_claim,
                "knowledge.claim.correct": _knowledge_correct,
                "knowledge.claim.stale": _knowledge_stale,
                "knowledge.claim.forget": _knowledge_forget,
                "composer.ask": _ask,
                "computer.action.request": _computer_action,
                "computer.action.confirm": _computer_confirm,
                "computer.action.deny": _computer_deny,
                "computer.action.history": lambda _params: self.computer_actions.history(),
                "rooms.list": lambda _params: _rooms_payload(),
                "rooms.get": _rooms_get,
                "devices.command": _device_command,
                "requests.approve": _request_approve,
                "requests.deny": _request_deny,
            }
        )

    def _build_director(self) -> HavenApplication:
        # Keep rebuilds on the same explicit mode: normal boot remains a real
        # installation even when every provider is currently unavailable.
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
        self.claims = ClaimStore(data_dir / "claims.db")
        self.knowledge = KnowledgeService(resources=self.resources, claims=self.claims, clock=self._director_clock)
        self.search = HavenSearchService(resources=self.resources, ontology=self.ontology, claims=self.claims)
        self.action_ledger = ActionLedgerStore(data_dir / "action_ledger.db")
        # These objects hold the installation root themselves; reconstruct
        # them too, otherwise a data-dir move would rebind the stores while
        # backup/service actions continued operating on the old directory.
        self.backups = BackupManager(data_dir=data_dir)
        self.service = StartupManager(
            data_dir=data_dir,
            port_getter=lambda: self.server_address[1],
        )
        self.setup.set_resource_store(self.resources)
        self.setup.set_knowledge_service(self.knowledge)
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
    def plugins(self) -> PluginManager:
        return self.server.plugins

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
    def service(self) -> StartupManager:
        return self.server.service

    @property
    def search(self) -> HavenSearchService:
        return self.server.search

    @property
    def computer_actions(self) -> ComputerActionService:
        return self.server.computer_actions

    def _authorize_session(self) -> bool:
        expected = self.server._session_token
        if expected is None:
            return True
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except (TypeError, ValueError):
            cookies = SimpleCookie()
        actual = cookies.get("haven_session")
        if actual is not None and hmac.compare_digest(actual.value, expected):
            return True
        self._send_json(401, {"ok": False, "error": "HAVEN desktop session required"})
        return False

    def _visible_scope_ids(self, params: dict, *, key: str) -> tuple[str, ...] | None:
        """Resolve the local visible-scope boundary for a web request.

        The wider ``IdentityProvider.memberships()`` contract is not wired
        yet, so a local HAVEN installation has exactly one visible scope: its
        current household.  An omitted filter means that scope; a caller may
        repeat it explicitly, but cannot turn a query parameter into an
        authorization grant for another scope.
        """

        requested = params.get(key, [])
        if not isinstance(requested, list) or any(
            not isinstance(scope_id, str) or not scope_id.strip() for scope_id in requested
        ):
            self._send_json(400, {"ok": False, "error": f"{key} must contain non-empty scope ids"})
            return None
        visible = (self.director.household_id,)
        if requested and set(requested) != set(visible):
            self._send_json(
                403,
                {
                    "ok": False,
                    "error": "the requested scope is not visible to this HAVEN installation",
                },
            )
            return None
        return visible

    def _claim_is_visible(self, claim) -> bool:
        return claim.scope_id == self.director.household_id

    def _handle_desktop_bootstrap(self) -> None:
        supplied = parse_qs(urlsplit(self.path).query).get("session", [""])[0]
        with self.server._bootstrap_lock:
            expected = self.server._bootstrap_token
            if expected is None or not hmac.compare_digest(supplied, expected):
                self._send_json(404, {"error": "not found"})
                return
            # A bootstrap URL is intentionally a one-shot capability.  Clear
            # it before writing the response so concurrent requests cannot
            # both obtain a valid desktop session.
            self.server._bootstrap_token = None
        session = self.server._session_token
        if session is None:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(303)
        self.send_header(
            "Set-Cookie",
            f"haven_session={session}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_desktop_activate(self) -> None:
        body = self._read_json()
        if body is None:
            return
        expected = self.server._activation_token
        supplied = body.get("token")
        if (
            expected is None
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, expected)
        ):
            self._send_json(404, {"error": "not found"})
            return
        handler = self.server._activation_handler
        activated = False
        if handler is not None:
            try:
                activated = bool(handler())
            except Exception:
                activated = False
        self._send_json(200, {"ok": True, "activated": activated})

    def _pick_folder(self) -> None:
        picker = self.server.folder_picker
        if picker is None:
            self._send_json(200, {"ok": False, "error": "native folder picker is available in HAVEN Desktop"})
            return
        try:
            selected = picker()
        except Exception:
            self._send_json(200, {"ok": False, "error": "native folder picker is unavailable"})
            return
        if not isinstance(selected, str) or not selected.strip():
            self._send_json(200, {"ok": False, "cancelled": True})
            return
        self._send_json(200, {"ok": True, "path": selected.strip()})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/__desktop_bootstrap":
            self._handle_desktop_bootstrap()
            return
        if not self._authorize_session():
            return
        if path == "/api/state":
            self._send_json(200, self.director.state())
        elif path == "/api/host/capabilities":
            self._send_json(200, self.server.host_capabilities())
        elif path == "/api/scheduler":
            self._send_json(200, {"ok": True, "scheduler": self.director.scheduler_status()})
        elif path == "/api/models":
            self._send_json(200, overview_payload(self.models))
        elif path == "/api/models/jobs":
            self._send_json(200, {"ok": True, "jobs": [job_to_dict(job) for job in self.model_jobs.list()]})
        elif path == "/api/plugins":
            self._send_json(200, plugins_current_payload(self.plugins))
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
        elif path == "/api/knowledge/claims":
            self._send_knowledge_claims(parse_qs(urlsplit(self.path).query))
        elif path == "/api/computer/actions/history":
            self._send_json(200, self.computer_actions.history())
        elif path == "/api/rooms":
            self._send_json(200, {"ok": True, "rooms": self.director.state()["rooms"]})
        elif path == "/api/people":
            self._send_json(200, self._people_payload())
        elif path == "/api/contexts":
            household = self.setup_service.status()["setup"]["household"]
            self._send_json(200, {"ok": True, "contexts": household.get("contexts", [])})
        elif path == "/api/automations":
            self._send_json(200, {"ok": True, "automations": self.director.state()["automations"]})
        elif path == "/api/automations/options":
            self._send_json(200, {"ok": True, "options": self.director.automation_options()})
        else:
            match = _KNOWLEDGE_CLAIM_PATH.match(path)
            if match:
                self._send_knowledge_claim(unquote(match.group(1)))
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
        if path == "/__desktop_activate":
            self._handle_desktop_activate()
            return
        if not self._authorize_session():
            return
        if path == "/api/chat":
            body = self._read_json()
            if body is None:
                return
            text = str(body.get("text", ""))
            mutation = self._apply_authoring_chat(
                text,
                body.get("focus"),
                body.get("automation_id"),
            )
            if mutation is not None:
                self._send_json(200 if mutation.get("ok") else 400, mutation)
                return
            state = self.director.chat(text, body.get("focus"))
            self._send_json(200, {"ok": True, "state": state})
            return
        if path == "/api/rooms" or path == "/api/people" or path == "/api/contexts" or path == "/api/automations":
            body = self._read_json()
            if body is None:
                return
            self._handle_authoring_post(path, body)
            return
        automation_action = _AUTHORING_AUTOMATION_ACTION_PATH.match(path)
        if automation_action:
            body = self._read_json(optional=True)
            if body is None:
                return
            rule_id, action = automation_action.groups()
            if action == "approve":
                result = self.director.approve_automation(
                    rule_id,
                    justification=body.get("justification", "owner approved automation from HAVEN"),
                )
            else:
                result = self.director.revoke_automation(
                    rule_id,
                    justification=body.get("justification", "owner revoked automation from HAVEN"),
                )
            self._send_setup_result(result)
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
        if path in ("/api/host/pick-folder", "/api/desktop/pick-folder"):
            self._pick_folder()
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
        if path.startswith("/api/plugins/"):
            self._handle_plugins_post(path)
            return
        if path == "/api/setup" or path.startswith("/api/setup/"):
            self._handle_setup_post(path)
            return
        if path == "/api/computer/actions" or path.startswith("/api/computer/actions/"):
            self._handle_computer_action_post(path)
            return
        if path.startswith("/api/knowledge/claims/"):
            self._handle_knowledge_post(path)
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

    def do_PATCH(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._authorize_session():
            return
        body = self._read_json()
        if body is None:
            return
        self._handle_authoring_patch(path, body)

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._authorize_session():
            return
        body = self._read_json(optional=True)
        if body is None:
            return
        self._handle_authoring_delete(path, body)

    def _people_payload(self) -> dict:
        household = self.setup_service.status()["setup"]["household"]
        present = {person["id"]: person for person in self.director.state().get("people", [])}
        people = []
        for declared in household.get("people", []):
            row = dict(declared)
            live = present.get(row.get("person_id"))
            row["present"] = live is not None
            row["room"] = live.get("room") if live is not None else None
            people.append(row)
        return {"ok": True, "people": people}

    def _apply_authoring_chat(
        self,
        text: str,
        focus: str | None,
        automation_id: str | None = None,
    ) -> dict | None:
        """Apply one conservative mutation proposal through authoring services.

        The parser only constructs a frozen proposal.  This adapter is the
        sole side-effecting step: it resolves human-facing names against the
        current declaration store and delegates every write to
        ``SetupService``.  A phrase it cannot resolve returns ``None`` so the
        ordinary governed intelligence/chat path remains unchanged.
        """

        proposal = parse_authoring_intent(
            text,
            focus=focus if isinstance(focus, str) else None,
            automation_focus=automation_id if isinstance(automation_id, str) else None,
        )
        if not isinstance(proposal, MutationProposal):
            return None
        attributes = dict(proposal.attributes)
        setup = self.setup_service

        if proposal.entity_kind == "room":
            name = attributes.get("name")
            if not isinstance(name, str):
                return None
            if proposal.operation == "create":
                result = setup.add_room(name=name)
            else:
                target_name = proposal.target_id or name
                room_id = setup.room_id_for_name(target_name)
                if room_id is None:
                    return {
                        "ok": False,
                        "error": f"I could not find a declared room named {target_name!r}",
                        "state": self.director.state(),
                    }
                if proposal.operation == "rename":
                    result = setup.rename_room(room_id=room_id, name=name)
                else:
                    result = setup.remove_room(room_id=room_id)
        elif proposal.entity_kind == "person" and proposal.operation == "create":
            name = attributes.get("name")
            role = attributes.get("role", "member")
            if not isinstance(name, str) or not isinstance(role, str):
                return None
            result = setup.declare_person(name=name, role=role)
        elif proposal.entity_kind == "context" and proposal.operation == "create":
            label = attributes.get("label")
            entity_id = attributes.get("entity_id")
            if not isinstance(label, str) or not isinstance(entity_id, str):
                return None
            result = setup.declare_context(label=label, entity_id=entity_id)
        elif proposal.entity_kind == "automation" and proposal.operation == "create":
            room = attributes.get("room")
            time_of_day = attributes.get("time_of_day")
            weekdays = attributes.get("weekdays", ())
            action = attributes.get("action")
            if (
                not isinstance(room, str)
                or not isinstance(time_of_day, str)
                or not isinstance(weekdays, (tuple, list))
                or action != "light.turn_off"
            ):
                return None
            device_ids = self.director.registry.find(role="light", room=room)
            if not device_ids:
                return {
                    "ok": False,
                    "error": f"I could not find a light in the {room}",
                    "state": self.director.state(),
                }
            if len(device_ids) > 1:
                return {
                    "ok": False,
                    "error": f"I found more than one light in the {room}; use the automation form to choose one",
                    "state": self.director.state(),
                }
            manifest = self.director.registry.get(device_ids[0])
            capability = next(
                (item for item in manifest.capabilities if item.service == action and item.writable),
                None,
            )
            if capability is None:
                return {
                    "ok": False,
                    "error": f"the light in the {room} does not expose a writable power-off capability",
                    "state": self.director.state(),
                }
            result = self.director.create_automation(
                source_text=proposal.source_text,
                time_of_day=time_of_day,
                weekdays=list(weekdays),
                target_device_id=manifest.device_id,
                capability=capability.name,
                service=capability.service,
                interpretation=proposal.source_text,
            )
        elif proposal.entity_kind == "automation" and proposal.operation == "update":
            remove_weekday = attributes.get("remove_weekday")
            if proposal.target_id is None or not isinstance(remove_weekday, int):
                return None
            try:
                rule = self.director.store.get_rule(proposal.target_id)
            except KeyError:
                return {
                    "ok": False,
                    "error": "I could not find the focused automation",
                    "state": self.director.state(),
                }
            schedule = rule.draft.schedule_trigger
            if schedule is None:
                return {
                    "ok": False,
                    "error": "the focused automation has no time schedule to edit",
                    "state": self.director.state(),
                }
            if getattr(rule.status, "value", None) != "proposed":
                return {
                    "ok": False,
                    "error": "approved automations are immutable; revoke it and create a new proposal",
                    "state": self.director.state(),
                }
            days = set(schedule.weekdays)
            if not days:
                days = set(range(7))
            if remove_weekday not in days:
                return {
                    "ok": False,
                    "error": "that automation already skips the requested day",
                    "state": self.director.state(),
                }
            days.remove(remove_weekday)
            if not days:
                return {
                    "ok": False,
                    "error": "that change would leave the automation with no scheduled weekdays",
                    "state": self.director.state(),
                }
            result = self.director.update_automation(
                proposal.target_id,
                source_text=proposal.source_text,
                time_of_day=schedule.time_of_day.isoformat(),
                weekdays=sorted(days),
                interpretation=f"{rule.draft.interpretation} (except weekday {remove_weekday})",
                justification="resident clarified the automation from the HAVEN composer",
            )
        else:
            # Other person, context, and automation operations are
            # intentionally not guessed yet.
            return None

        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "HAVEN could not apply that change"),
                "state": self.director.state(),
            }
        payload = {
            "ok": True,
            "authoring": {
                "entity_kind": proposal.entity_kind,
                "operation": proposal.operation,
                "source_text": proposal.source_text,
            },
            "state": self.director.state(),
        }
        if isinstance(result.get("automation"), dict):
            payload["automation"] = result["automation"]
        return payload

    def _handle_authoring_post(self, path: str, body: dict) -> None:
        setup = self.setup_service
        if path == "/api/rooms":
            result = setup.add_room(name=body.get("name"))
        elif path == "/api/people":
            role = body.get("role")
            result = setup.declare_person(
                name=body.get("name"),
                entity_id=body.get("entity_id"),
                room_id=body.get("room_id"),
                role=role if isinstance(role, str) and role.strip() else "member",
            )
        elif path == "/api/contexts":
            result = setup.declare_context(label=body.get("label"), entity_id=body.get("entity_id"))
        elif path == "/api/automations":
            weekdays = body.get("weekdays", [])
            result = self.director.create_automation(
                source_text=body.get("source_text"),
                time_of_day=body.get("time_of_day"),
                weekdays=weekdays if isinstance(weekdays, list) else [],
                target_device_id=body.get("target_device_id"),
                capability=body.get("capability"),
                service=body.get("service"),
                interpretation=body.get("interpretation"),
                parameters=body.get("parameters") if isinstance(body.get("parameters"), dict) else None,
            )
        else:
            self._send_json(404, {"error": "not found"})
            return
        self._send_setup_result(result)

    def _handle_authoring_patch(self, path: str, body: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api":
            self._send_json(404, {"error": "not found"})
            return
        collection, item_id = parts[1], unquote(parts[2])
        if collection == "rooms":
            result = self.setup_service.rename_room(room_id=item_id, name=body.get("name"))
        elif collection == "people":
            result = self.setup_service.update_person(
                person_id=item_id,
                name=body.get("name"),
                role=body.get("role"),
            )
        elif collection == "contexts":
            result = self.setup_service.update_context(
                context_id=item_id,
                label=body.get("label"),
                entity_id=body.get("entity_id"),
            )
        elif collection == "automations":
            if isinstance(body.get("enabled"), bool):
                rule = next((item for item in self.director.store.state.rules if item.rule_id == item_id), None)
                if rule is None:
                    self._send_json(404, {"ok": False, "error": "unknown automation"})
                    return
                if getattr(rule.status, "value", None) != "approved":
                    self._send_json(400, {"ok": False, "error": "only approved automations can be enabled or disabled"})
                    return
                result = {
                    "ok": True,
                    "scheduler": self.director.set_scheduler_enabled(item_id, body["enabled"]),
                    "state": self.director.state(),
                }
            else:
                result = self.director.update_automation(
                    item_id,
                    source_text=body.get("source_text"),
                    time_of_day=body.get("time_of_day"),
                    weekdays=body.get("weekdays", []),
                    interpretation=body.get("interpretation"),
                    parameters=body.get("parameters") if isinstance(body.get("parameters"), dict) else None,
                    justification=body.get("justification", "edited automation in HAVEN"),
                )
        else:
            self._send_json(404, {"error": "not found"})
            return
        self._send_setup_result(result)

    def _handle_authoring_delete(self, path: str, body: dict) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api":
            self._send_json(404, {"error": "not found"})
            return
        collection, item_id = parts[1], unquote(parts[2])
        if collection == "rooms":
            result = self.setup_service.remove_room(room_id=item_id)
        elif collection == "people":
            result = self.setup_service.remove_person(person_id=item_id)
        elif collection == "contexts":
            result = self.setup_service.remove_context(context_id=item_id)
        elif collection == "automations":
            justification = body.get("justification")
            if not isinstance(justification, str) or not justification.strip():
                self._send_json(400, {"ok": False, "error": "automation deletion requires a justification"})
                return
            result = self.director.revoke_automation(item_id, justification=justification)
        else:
            self._send_json(404, {"error": "not found"})
            return
        self._send_setup_result(result)

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

    def _handle_plugins_post(self, path: str) -> None:
        if path == "/api/plugins/refresh":
            try:
                self._send_json(200, plugins_refresh_payload(self.plugins))
            except Exception as exc:
                self._send_json(200, {"ok": False, "error": str(exc)})
            return
        match = _PLUGIN_ENABLE_PATH.match(path) or _PLUGIN_DISABLE_PATH.match(path)
        if not match:
            self._send_json(404, {"error": "not found"})
            return
        plugin_id = unquote(match.group(1))
        action = self.plugins.enable if _PLUGIN_ENABLE_PATH.match(path) else self.plugins.disable
        try:
            view = action(plugin_id)
        except Exception as exc:
            # UnknownPluginError (unlisted id) and InvalidPluginIdError (bad
            # shape) both land here: an operational failure the UI shows
            # inline, never a traceback.
            self._send_json(200, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, plugins_marketplace_payload(view))

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
        scope_ids = self._visible_scope_ids(params, key="scope")
        if scope_ids is None:
            return
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
                        "resource": (
                            resource_record_to_dict(resource)
                            if (resource := self.server.resources.get(hit.resource_id)) is not None
                            else None
                        ),
                        "matched_claims": [
                            self._claim_payload(claim)
                            for ref in hit.matched_refs
                            if (claim := self.server.claims.get(ref)) is not None
                        ],
                    }
                    for hit in hits
                ],
            },
        )

    def _claim_payload(self, claim, *, detail: bool = False) -> dict:
        payload = claim_to_dict(claim)
        payload["fingerprint"] = claim_fingerprint(claim)
        if detail:
            payload["sources"] = [
                {
                    "ref": ref,
                    "resource": (
                        resource_record_to_dict(resource)
                        if (resource := self.server.resources.get(ref)) is not None
                        else None
                    ),
                }
                for ref in claim.source_refs
            ]
            payload["contradictions"] = [
                claim_to_dict(found) for found in self.server.claims.contradictions_of(claim.claim_id)
            ]
            payload["superseded_claims"] = [
                claim_to_dict(found) for found in self.server.claims.supersedes_of(claim.claim_id)
            ]
            payload["audit"] = [
                audit_event_to_dict(event)
                for event in self.server.claims.list_audit(
                    scope_id=claim.scope_id,
                    claim_id=claim.claim_id,
                )
            ]
        return payload

    def _send_knowledge_claims(self, params: dict) -> None:
        scope_ids = self._visible_scope_ids(params, key="scope")
        if scope_ids is None:
            return
        scope_id = scope_ids[0]
        state_value = params.get("state", [None])[0] or None
        include_stale = params.get("include_stale", ["false"])[0].strip().lower() in ("1", "true", "yes")
        try:
            state = ClaimState(state_value) if state_value else None
        except ValueError:
            self._send_json(400, {"ok": False, "error": f"unknown claim state: {state_value}"})
            return
        claims = self.server.knowledge.list_claims(scope_id=scope_id, include_stale=include_stale)
        if state is not None:
            claims = tuple(claim for claim in claims if claim.state is state)
        try:
            limit = max(1, min(200, int(params.get("limit", ["50"])[0])))
        except ValueError:
            limit = 50
        self._send_json(200, {"ok": True, "claims": [self._claim_payload(claim) for claim in claims[:limit]]})

    def _send_knowledge_claim(self, claim_id: str) -> None:
        claim = self.server.claims.get(claim_id)
        if claim is None or not self._claim_is_visible(claim):
            self._send_json(404, {"ok": False, "error": "unknown claim"})
            return
        self._send_json(200, {"ok": True, "claim": self._claim_payload(claim, detail=True)})

    def _handle_knowledge_post(self, path: str) -> None:
        match = _KNOWLEDGE_CLAIM_ACTION_PATH.match(path)
        if match is None:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        body = self._read_json(optional=True)
        if body is None:
            return
        claim_id, action = (unquote(value) for value in match.groups())
        claim = self.server.claims.get(claim_id)
        if claim is None or not self._claim_is_visible(claim):
            self._send_json(404, {"ok": False, "error": "unknown claim"})
            return
        # Never trust an actor id supplied in the request body. Knowledge
        # changes are owner-bound just like governed actions; the running
        # household's declared principal is the only actor source.
        if not getattr(self.director, "has_declared_owner", False):
            self._send_json(400, {"ok": False, "error": "a declared owner is required for knowledge changes"})
            return
        principal = getattr(self.director, "owner", None)
        actor = getattr(principal, "actor_id", None)
        if not isinstance(actor, str) or not actor.strip() or actor == "no_owner_declared":
            self._send_json(400, {"ok": False, "error": "a declared owner is required for knowledge changes"})
            return
        if action == "correct":
            proposition = body.get("proposition")
            if not isinstance(proposition, str) or not proposition.strip():
                self._send_json(400, {"ok": False, "error": "a non-empty 'proposition' is required"})
                return
            result = self.server.knowledge.correct_claim(
                claim_id, proposition=proposition, actor=actor.strip()
            )
            if result.claim is None:
                self._send_json(400, {"ok": False, "error": result.reason or "correction rejected"})
                return
            self._send_json(200, {"ok": True, "claim": self._claim_payload(result.claim)})
            return
        if action == "stale":
            changed = self.server.knowledge.mark_claim_stale(claim_id, actor=actor.strip())
            self._send_json(200, {"ok": True, "changed": changed, "claim": self._claim_payload(self.server.claims.get(claim_id))})
            return
        self.server.knowledge.forget_claim(claim, forgotten_by=actor.strip())
        self._send_json(200, {"ok": True, "claim": self._claim_payload(self.server.claims.get(claim_id))})

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
    session_token: str | None = None,
    bootstrap_token: str | None = None,
    activation_token: str | None = None,
    on_activate=None,
    folder_picker=None,
    before_data_dir_changed=None,
    on_data_dir_changed=None,
    on_data_dir_change_failed=None,
) -> tuple[HavenWebServer, HavenApplication]:
    root = Path(static_root) if static_root is not None else Path(__file__).parent / "static"
    server = HavenWebServer(
        ("127.0.0.1", port), root, clock=clock, models_root=models_root, data_dir=data_dir,
        demo=demo, ha_client=ha_client, session_token=session_token, folder_picker=folder_picker,
        bootstrap_token=bootstrap_token,
        activation_token=activation_token,
        on_activate=on_activate,
        before_data_dir_changed=before_data_dir_changed,
        on_data_dir_changed=on_data_dir_changed,
        on_data_dir_change_failed=on_data_dir_change_failed,
    )
    return server, server.director


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the HAVEN local web surface.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="force the explicit demo household; normal boot builds the real installation",
    )
    args = parser.parse_args(argv)
    # The ordinary local web launcher still runs the shared renderer in a
    # browser, but it is a local host and can offer the same native folder
    # picker as HAVEN Desktop. Setup remains the authority boundary after the
    # picker returns a path.
    server, _ = make_server(args.port, demo=args.demo, folder_picker=choose_folder)
    host, port = server.server_address
    print(
        f"HAVEN web surface (development/compatibility only; the native "
        f"WinUI client is the production experience) listening on "
        f"http://{host}:{port}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()


__all__ = ["HavenWebServer", "make_server", "main"]
