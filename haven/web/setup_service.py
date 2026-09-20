"""The first-run onboarding workflow behind `/api/setup`.

The demo world stays untouched: these steps persist HAVEN's own installation
config (data dir, provider, preferences) and enroll demo discovery candidates
into the director's device registry. When a Home Assistant state source is
attached (the live adapter, at boot), the discovery scan also lists the
provider's real entities as candidates. A real BLE/mDNS scan is a future
native seam; the demo rows of the scan are honestly labeled demo candidates
in the shape a real transport would produce.

HAVEN has one root: choosing a data directory moves the installation (config,
token sidecar, enrolled-devices sidecar), it does not split it. The enrolled
sidecar persists full device manifests (version 2), because a summary that
cannot rebuild a manifest is not persistence; the legacy v1 summary shape is
migrated eagerly on load.

The household sidecar persists who lives here and what context entities mean:
people with the occupancy entities that report them, contexts with the labels
a deployment gave them. Presence and context meaning are declared, never
inferred, so the observer is wired from this file at boot.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..devices import CapabilityDescriptor, ControlClass, DeviceManifest
from ..discovery.enrollment import enroll_device
from ..discovery.models import DiscoveredDevice
from ..integrations.home_assistant.client import LiveHomeAssistantAdapter
from ..providers.plugin import ProviderManifest
from .computer_provider import (
    ComputerProviderConfig,
    build_filesystem_provider,
    load_computer_provider_config,
    save_computer_provider_config,
)
from .installation_files import installation_file_names
from .provider_install import (
    find_installed_provider,
    is_real_installation,
    load_installed_providers,
    remove_installed_provider,
    save_installed_provider,
    set_installed_provider_enabled,
)
from .setup_config import (
    SetupConfig,
    SetupConfigError,
    SetupConfigStore,
    _write_json_atomic,
    default_data_dir,
)


def _provider_manifest_to_dict(manifest: ProviderManifest) -> dict:
    return {
        "provider_id": manifest.provider_id,
        "kind": manifest.kind,
        "capabilities": sorted(manifest.capabilities),
        "display_name": manifest.display_name,
        "description": manifest.description,
        "permissions": list(manifest.permissions),
        "version": manifest.version,
        "homepage": manifest.homepage,
        "config_fields": [
            {"name": f.name, "label": f.label, "required": f.required, "secret": f.secret}
            for f in manifest.config_fields
        ],
    }

_ENROLL_JUSTIFICATION = "enrolled from the setup wizard discovery scan"
_TOKEN_FILENAME = "ha_token.txt"
_ENROLLED_FILENAME = "enrolled_devices.json"
_ENROLLED_VERSION = 2
_HOUSEHOLD_FILENAME = "household.json"
_HOUSEHOLD_VERSION = 1
_RULES_FILENAME = "rules.json"


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class DeclaredPresenceSource:
    """One occupancy entity declaring "this entity reports a person in a room"."""

    entity_id: str
    room_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _require_text(self.entity_id, name="entity_id"))
        object.__setattr__(self, "room_id", _require_text(self.room_id, name="room_id"))


_DECLARED_ROLES = ("owner", "member")


@dataclass(frozen=True)
class DeclaredPerson:
    """A household member and the occupancy entities that report their presence.

    `role` is the person's standing in the household: exactly "owner" or
    "member". Sidecar rows written before roles existed load as "member".
    """

    person_id: str
    name: str
    sources: tuple[DeclaredPresenceSource, ...] = ()
    role: str = "member"

    def __post_init__(self) -> None:
        object.__setattr__(self, "person_id", _require_text(self.person_id, name="person_id"))
        object.__setattr__(self, "name", _require_text(self.name, name="name"))
        object.__setattr__(self, "sources", tuple(self.sources))
        if self.role not in _DECLARED_ROLES:
            raise ValueError(f"role must be one of {_DECLARED_ROLES}, got {self.role!r}")
        object.__setattr__(self, "role", self.role)


@dataclass(frozen=True)
class DeclaredContext:
    """A household context and the entity whose "on" means the context is active.

    Contexts are declared meaning, not wiring: several contexts may share one
    entity (one switch, several meanings), so nothing here is keyed on entity.
    """

    context_id: str
    label: str
    entity_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _require_text(self.context_id, name="context_id"))
        object.__setattr__(self, "label", _require_text(self.label, name="label"))
        object.__setattr__(self, "entity_id", _require_text(self.entity_id, name="entity_id"))


@dataclass(frozen=True)
class HouseholdDeclarations:
    """Who lives here and what context entities mean, as the household declared."""

    people: tuple[DeclaredPerson, ...] = ()
    contexts: tuple[DeclaredContext, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "people", tuple(self.people))
        object.__setattr__(self, "contexts", tuple(self.contexts))


def _derived_declared_id(text: str) -> str:
    """`text` lowercased, non-alphanumerics as `_`, runs collapsed: "Gerron Smith" -> "gerron_smith"."""

    collapsed: list[str] = []
    for char in text.lower():
        char = char if char.isalnum() else "_"
        if char == "_" and collapsed and collapsed[-1] == "_":
            continue
        collapsed.append(char)
    return "".join(collapsed).strip("_")


def _household_payload(declarations: HouseholdDeclarations) -> dict:
    return {
        "people": [
            {
                "person_id": person.person_id,
                "name": person.name,
                "role": person.role,
                "sources": [
                    {"entity_id": source.entity_id, "room_id": source.room_id}
                    for source in person.sources
                ],
            }
            for person in declarations.people
        ],
        "contexts": [
            {"context_id": context.context_id, "label": context.label, "entity_id": context.entity_id}
            for context in declarations.contexts
        ],
    }


def load_household_declarations(path: Path) -> HouseholdDeclarations:
    """Read the household sidecar: who lives here, what context entities mean.

    A missing file means "nobody declared yet". A structurally unreadable file
    (bad JSON, not an object, wrong version, lists that are not lists) raises
    `SetupConfigError`; callers load it lazily and degrade to empty, the same
    contract `load_enrolled_sidecar` keeps for boot. Individual rows that
    cannot be parsed are skipped rather than taking the whole file down.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return HouseholdDeclarations()
    except UnicodeDecodeError as exc:
        raise SetupConfigError(f"household declarations are not valid UTF-8: {path}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SetupConfigError(f"household declarations are not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise SetupConfigError("household declarations must be a JSON object")
    version = payload.get("version")
    if version != _HOUSEHOLD_VERSION:
        raise SetupConfigError(f"unsupported household declarations version: {version!r}")
    raw_people = payload.get("people", [])
    raw_contexts = payload.get("contexts", [])
    if not isinstance(raw_people, list) or not isinstance(raw_contexts, list):
        raise SetupConfigError("household declarations 'people' and 'contexts' must be lists")
    people: list[DeclaredPerson] = []
    for entry in raw_people:
        if not isinstance(entry, dict):
            continue
        try:
            raw_sources = entry.get("sources", [])
            sources = tuple(
                DeclaredPresenceSource(entity_id=row["entity_id"], room_id=row["room_id"])
                for row in raw_sources
                if isinstance(row, dict)
            )
            people.append(
                DeclaredPerson(
                    person_id=entry["person_id"],
                    name=entry["name"],
                    sources=sources,
                    role=entry.get("role", "member"),
                )
            )
        except (KeyError, ValueError):
            continue
    contexts: list[DeclaredContext] = []
    for entry in raw_contexts:
        if not isinstance(entry, dict):
            continue
        try:
            contexts.append(
                DeclaredContext(
                    context_id=entry["context_id"],
                    label=entry["label"],
                    entity_id=entry["entity_id"],
                )
            )
        except (KeyError, ValueError):
            continue
    return HouseholdDeclarations(people=tuple(people), contexts=tuple(contexts))

# Household members supply capabilities at enrollment time, the same way an
# owner supplies a justification to approve a rule: a scan suggestion is not
# a decision, so each preset is the explicit capability set for one type.
_CAPABILITY_PRESETS: dict[str, tuple[CapabilityDescriptor, ...]] = {
    "light": (
        CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
        CapabilityDescriptor("brightness", ControlClass.MEDIUM, writable=True, service="light.set_brightness"),
    ),
    "thermostat": (
        CapabilityDescriptor("temperature", ControlClass.MEDIUM, writable=True, service="climate.set_temperature"),
    ),
    "switch": (
        CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="switch.turn_off"),
    ),
    "fan": (
        CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="fan.turn_off"),
    ),
    "cover": (
        CapabilityDescriptor("close", ControlClass.GUARDED, writable=True, service="cover.close"),
        CapabilityDescriptor("open", ControlClass.GUARDED, writable=True, service="cover.open"),
    ),
    # A camera discovered through the setup wizard's HA-state scan is
    # observation-only: HA's camera domain doesn't expose PTZ/privacy-shutter
    # itself (those are separate entities when they exist at all), so this
    # preset never claims control this discovery path can't back. The richer
    # `haven.cameras` PTZ/privacy-shutter bridge is for cameras discovered as
    # hardware, a different discovery path from this one.
    "camera": (CapabilityDescriptor("live_stream", ControlClass.READ, readable=True),),
}


@dataclass(frozen=True)
class _EnrolledLoad:
    """The result of reading the enrolled-devices sidecar."""

    manifests: tuple[DeviceManifest, ...]
    rows: dict[str, dict]  # candidate_id -> status row (the frontend contract)
    migrated: bool  # legacy v1 rows were upgraded and the sidecar should be rewritten


def _enrolled_v2_payload(manifests: tuple[DeviceManifest, ...]) -> dict:
    return {
        "version": _ENROLLED_VERSION,
        "manifests": [manifest.to_dict() for manifest in manifests],
    }


def load_enrolled_sidecar(path: Path) -> _EnrolledLoad:
    """Read the enrolled-devices sidecar, migrating the legacy v1 shape.

    Version 2 stores full `DeviceManifest` dicts; version 1 stored only a
    summary (candidate_id, device_id, device_type, room). A summary that
    cannot rebuild a manifest is not persistence, so v1 is migrated eagerly:
    each row becomes a manifest with capabilities from the enrollment presets
    and provider_id "demo.legacy", and `migrated` tells the caller to rewrite
    the sidecar in v2 immediately. Unknown device types are skipped -- a row
    that cannot become a manifest is dropped rather than guessed at.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _EnrolledLoad(manifests=(), rows={}, migrated=False)
    try:
        payload = json.loads(raw)
    except ValueError:
        return _EnrolledLoad(manifests=(), rows={}, migrated=False)
    if not isinstance(payload, dict):
        return _EnrolledLoad(manifests=(), rows={}, migrated=False)
    if payload.get("version") == _ENROLLED_VERSION:
        manifests: list[DeviceManifest] = []
        rows: dict[str, dict] = {}
        raw_manifests = payload.get("manifests")
        if not isinstance(raw_manifests, list):
            return _EnrolledLoad(manifests=(), rows={}, migrated=False)
        for entry in raw_manifests:
            if not isinstance(entry, dict):
                continue
            try:
                manifest = DeviceManifest.from_dict(entry)
            except ValueError:
                continue
            manifests.append(manifest)
            rows[manifest.device_id] = _enrolled_row(manifest.device_id, manifest)
        return _EnrolledLoad(manifests=tuple(manifests), rows=rows, migrated=False)
    # Legacy v1: {"enrolled": [{candidate_id, device_id, device_type, room}]}.
    entries = payload.get("enrolled")
    if not isinstance(entries, list):
        return _EnrolledLoad(manifests=(), rows={}, migrated=False)
    manifests = []
    rows = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("candidate_id"), str):
            continue
        device_type = entry.get("device_type")
        capabilities = _CAPABILITY_PRESETS.get(device_type) if isinstance(device_type, str) else None
        if capabilities is None:
            continue
        device_id = entry.get("device_id")
        room = entry.get("room")
        manifest = DeviceManifest(
            device_id=device_id if isinstance(device_id, str) and device_id.strip() else entry["candidate_id"],
            device_type=device_type,
            provider_id="demo.legacy",
            capabilities=capabilities,
            room=room if isinstance(room, str) and room.strip() else None,
        )
        manifests.append(manifest)
        rows[manifest.device_id] = _enrolled_row(manifest.device_id, manifest)
    return _EnrolledLoad(manifests=tuple(manifests), rows=rows, migrated=bool(manifests))


def _enrolled_row(candidate_id: str, manifest: DeviceManifest) -> dict:
    return {
        "candidate_id": candidate_id,
        "device_id": manifest.device_id,
        "device_type": manifest.device_type,
        "room": manifest.room,
    }


@dataclass(frozen=True)
class _DemoScanCandidate:
    """The static half of a demo discovery candidate; a scan stamps the time."""

    candidate_id: str
    source: str
    suggested_device_type: str | None
    suggested_room: str | None
    signal_strength: float | None


# The demo discovery scan's standing candidates. A real BLE/mDNS/WiFi scan is
# a future native seam, so these are labeled demo data rather than disguised
# as live radio observations.
_DEMO_SCAN_CANDIDATES: tuple[_DemoScanCandidate, ...] = (
    _DemoScanCandidate("ble:bulb-a1f2", "demo.scan.ble", "light", "office", -52),
    _DemoScanCandidate("mdns:therm-living", "demo.scan.mdns", "thermostat", "living_room", None),
    _DemoScanCandidate("wifi:plug-heater", "demo.scan.wifi", "switch", "bedroom", -61),
)

# Domains the setup scan enrolls from Home Assistant state. Presence sensors,
# automations, and plain sensors are perception, not actuation, so they are
# not enrollment candidates.
_HA_DISCOVERY_DEVICE_TYPES: dict[str, str] = {
    "light": "light",
    "switch": "switch",
    "climate": "thermostat",
    "cover": "cover",
    "camera": "camera",
    "fan": "fan",
}
_HA_DEAD_STATES = frozenset({"unavailable", "unknown"})
_HA_PROVIDER_ID = "home_assistant"
_HA_SOURCE = "home_assistant.states"


def ha_candidates_from_states(states: tuple[dict, ...], *, now: datetime) -> tuple[DiscoveredDevice, ...]:
    """Map Home Assistant state dicts to enrollment candidates, one per entity.

    Only controllable domains are listed; `unavailable`/`unknown` entities are
    skipped -- a dead entity is not a candidate. HA discovery is registry
    listing, not proximity: it enumerates what the provider already knows, so
    there is no signal strength.
    """

    candidates: list[DiscoveredDevice] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str):
            continue
        domain, sep, name = entity_id.partition(".")
        device_type = _HA_DISCOVERY_DEVICE_TYPES.get(domain) if sep else None
        if device_type is None or state.get("state") in _HA_DEAD_STATES:
            continue
        tokens = name.split("_")
        candidates.append(
            DiscoveredDevice(
                candidate_id=entity_id,
                provider_id=_HA_PROVIDER_ID,
                discovered_at=now,
                source=_HA_SOURCE,
                suggested_device_type=device_type,
                suggested_room=tokens[0] if len(tokens) >= 2 else None,
                signal_strength=None,
            )
        )
    return tuple(candidates)


@dataclass(frozen=True)
class SetupCandidate:
    """One row of the setup wizard's discovery list, as shown to the user."""

    candidate_id: str
    provider_id: str
    source: str
    suggested_device_type: str | None
    suggested_room: str | None
    signal_strength: float | None
    discovered_at: str
    enrolled: bool = False


class SetupService:
    """Owns the setup steps and their persistence; every step answers a dict.

    Mutations return the full status envelope on success and
    `{"ok": False, "error": ...}` on a refused step, so the web layer can map
    ok:false straight onto a 400 without a second code path.
    """

    def __init__(
        self,
        *,
        store: SetupConfigStore,
        director,
        clock: Callable[[], datetime] | None = None,
        ha_states_source=None,
        on_rebuild: Callable[[], None] | None = None,
        on_data_dir_changed: Callable[[Path], None] | None = None,
        include_demo_candidates: bool = False,
        resource_store=None,
        knowledge_service=None,
    ) -> None:
        self._store = store
        self._director = director
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Structural: anything with `fetch_states() -> tuple[dict, ...]`, the
        # same contract `LiveHomeAssistantAdapter` implements.
        self._ha_states_source = ha_states_source
        # The "life search bar" substrate's own resource store -- optional
        # so a bare `SetupService` (most tests) never needs one just to
        # exercise the parts of setup that have nothing to do with it.
        self._resource_store = resource_store
        self._knowledge_service = knowledge_service
        # Called after a step changes provider connection, so the composition
        # root can rebuild the live household from the config this step just
        # saved instead of leaving the running app on whatever it built at
        # boot until someone restarts the process.
        self._on_rebuild = on_rebuild
        # DesktopShell uses this to move its held instance lock along with
        # the installation when onboarding changes the data root.
        self._on_data_dir_changed = on_data_dir_changed
        # False by default: a real household's first-run scan should never
        # show `ble:bulb-a1f2`/`mdns:therm-living`/`wifi:plug-heater` as if
        # they were real nearby devices -- those are the demo trial's own
        # fixture set. Only an actual `--demo` run (`make_server(demo=True)`)
        # sets this true.
        self._include_demo_candidates = include_demo_candidates
        self._config_error: str | None = None
        self._config = self._load_config()
        self._enrolled: dict[str, dict] = self._load_enrolled()
        self.household: HouseholdDeclarations = self._load_household()
        self._last_scan: tuple[SetupCandidate, ...] | None = None

    def attach_ha_states_source(self, source) -> None:
        """Wire the Home Assistant state source used by discovery scans.

        The composition root calls this after it builds the live adapter, so a
        scan can list real HA entities alongside the demo candidates.
        """

        self._ha_states_source = source

    def set_director(self, director) -> None:
        """Rebind to a freshly built director after the composition root rebuilds it.

        Called by `HavenWebServer.rebuild_director` immediately after it
        swaps the live director, so enrollment and status calls that follow
        act on the current household rather than the one that existed before
        this step changed provider connection.
        """

        self._director = director

    def set_resource_store(self, resource_store) -> None:
        """Rebind to a freshly built resource store after a data-dir move.

        `self._resource_store` is not derived from `self._director` (the
        resource/ontology substrate lives on `HavenWebServer` directly, not
        the governed household loop), so `set_director` alone would leave a
        `scan_computer_provider()` call writing into the *old* location's
        `ResourceStore` object after `choose_data_dir` moved
        `resources.db` out from under it.
        """

        self._resource_store = resource_store

    def set_knowledge_service(self, knowledge_service) -> None:
        """Rebind knowledge to stores rebuilt after a data-dir move."""

        self._knowledge_service = knowledge_service

    def _trigger_rebuild(self) -> None:
        if self._on_rebuild is not None:
            self._on_rebuild()

    def status(self) -> dict:
        config = self._config
        resolved = Path(config.data_dir) if config.data_dir is not None else default_data_dir()
        envelope = {
            "ok": True,
            "setup": {
                "completed": config.completed,
                "data_dir": {
                    "source": "chosen" if config.data_dir is not None else "default",
                    "resolved": str(resolved),
                },
                "provider": {
                    # Home Assistant's own dedicated field, never
                    # `provider_kind` -- that field no longer means "the"
                    # active provider (see `is_real_installation`), and
                    # this object is specifically the Home Assistant
                    # connect step's own status, not a household-wide
                    # summary (`list_provider_packages()` covers installed
                    # community providers separately).
                    "configured": config.provider_base_url is not None,
                    "kind": "home_assistant" if config.provider_base_url is not None else None,
                    "base_url": config.provider_base_url,
                },
                "discovery": {
                    "candidates": [asdict(candidate) for candidate in self._last_scan]
                    if self._last_scan is not None
                    else [],
                    "enrolled": list(self._enrolled.values()),
                },
                "computer": self._computer_provider_payload(),
                "preferences": {
                    "voice": config.voice_enabled,
                    "intelligence": config.intelligence_enabled,
                },
                "household": _household_payload(self.household),
            },
        }
        if self._config_error is not None:
            envelope["config_error"] = self._config_error
        return envelope

    def choose_data_dir(self, path: str | None) -> dict:
        if not path or not path.strip():
            resolved = default_data_dir()
        else:
            resolved = Path(path).expanduser().resolve()
            if resolved.is_file():
                return {"ok": False, "error": f"data dir exists as a file: {resolved}"}
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"could not create data dir {resolved}: {exc}"}
        probe = resolved / ".haven-write-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return {"ok": False, "error": f"data dir is not writable {resolved}: {exc}"}
        # HAVEN has one root: choosing a directory moves the installation, it
        # does not split it. Every sidecar `installation_file_names` knows
        # about (config, provider token, enrolled-devices and household
        # sidecars, the automations sidecar, durable history, resources,
        # ontology, the computer provider's own config, every installed
        # community provider's config/secrets) plus the backups directory
        # all move to the new root, and the store rebinds to the moved
        # haven.json so every path derived from store.path.parent is in the
        # new root immediately.
        current_dir = self._config_dir()
        if resolved != current_dir.resolve():
            names = installation_file_names(current_dir) + ("backups",)
            for name in names:
                source = current_dir / name
                if not source.exists():
                    continue
                try:
                    os.replace(source, resolved / name)
                except OSError as exc:
                    return {"ok": False, "error": f"could not move {name} to {resolved}: {exc}"}
            self._store.path = resolved / "haven.json"
        self._config = replace(self._config, data_dir=str(resolved))
        error = self._save()
        if error is not None:
            return {"ok": False, "error": error}
        # The server's own resource/ontology stores (and search service
        # built over them) are not part of `self._director` -- they live on
        # `HavenWebServer` directly -- so a plain director rebuild would
        # miss them. `_trigger_rebuild` covers both: `rebuild_director`
        # already rebuilds the resource/ontology stores from the current
        # data dir every time it runs.
        self._trigger_rebuild()
        if self._on_data_dir_changed is not None:
            self._on_data_dir_changed(resolved)
        return self.status()

    def connect_provider(
        self,
        *,
        kind: str | None,
        base_url: str | None,
        token: str | None,
        skip: bool = False,
    ) -> dict:
        if skip:
            # Disconnecting must not leave the credential behind: a stale
            # `ha_token.txt` on disk after the household said "forget this
            # connection" is a real privacy leftover, not a harmless orphan
            # file, even though nothing in HAVEN would read it once
            # `provider_token_file` is cleared.
            if self._config.provider_token_file:
                try:
                    (self._config_dir() / self._config.provider_token_file).unlink()
                except OSError:
                    pass
            self._config = replace(
                self._config,
                provider_kind=None,
                provider_base_url=None,
                provider_token_file=None,
            )
            error = self._save()
            if error is not None:
                return {"ok": False, "error": error}
            self._trigger_rebuild()
            return self.status()
        if kind != "home_assistant":
            return {"ok": False, "error": f"unsupported provider kind: {kind!r}"}
        if not isinstance(base_url, str) or not base_url.strip():
            return {"ok": False, "error": "a non-empty 'base_url' is required"}
        if not isinstance(token, str) or not token.strip():
            return {"ok": False, "error": "a non-empty 'token' is required"}
        adapter = LiveHomeAssistantAdapter(base_url=base_url, access_token=token)
        try:
            adapter.fetch_states()
        except Exception as exc:
            # Adapter errors carry URLs and reasons, never the token; keep it
            # that way rather than stringifying the request.
            return {"ok": False, "error": f"could not reach the Home Assistant provider: {exc}"}
        try:
            config_dir = self._config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            token_path = config_dir / _TOKEN_FILENAME
            token_path.write_text(token, encoding="utf-8")
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                # Advisory even on POSIX and meaningless on Windows, where the
                # bits exist but no permission boundary honors them.
                pass
        except OSError as exc:
            return {"ok": False, "error": f"could not persist the provider token: {exc}"}
        self._config = replace(
            self._config,
            provider_kind="home_assistant",
            provider_base_url=base_url.strip().rstrip("/"),
            provider_token_file=_TOKEN_FILENAME,
        )
        error = self._save()
        if error is not None:
            return {"ok": False, "error": error}
        self._trigger_rebuild()
        return self.status()

    def list_provider_packages(self) -> dict:
        """Every `haven.providers` entry point installed in this Python
        environment, each with its manifest (capabilities, permissions,
        config fields to ask for) and whether this installation has already
        activated it -- the listing behind Settings -> Providers.

        Reads package metadata and, to get each manifest, imports each
        package's `describe()` (side-effect-free by contract, see
        `haven/providers/plugin.py`); it never calls `build()`, so nothing
        real is constructed just by looking at this list. A package that
        fails to import or describe itself is still listed, with `error`
        set instead of `manifest`, rather than silently dropped -- a broken
        community package should be visible, not invisible.
        """

        from haven.providers.loader import ProviderLoadError, discover_provider_packages, inspect_provider_package

        installed_by_entry_point = {p.entry_point_name: p for p in load_installed_providers(self._store)}
        rows = []
        for discovered in discover_provider_packages():
            row: dict = {
                "entry_point_name": discovered.entry_point_name,
                "distribution_name": discovered.distribution_name,
                "distribution_version": discovered.distribution_version,
            }
            try:
                manifest = inspect_provider_package(discovered)
            except ProviderLoadError as exc:
                row["error"] = str(exc)
            else:
                row["manifest"] = _provider_manifest_to_dict(manifest)
                installed = installed_by_entry_point.get(discovered.entry_point_name)
                row["installed"] = installed is not None
                row["enabled"] = installed.enabled if installed is not None else False
                # "Active" means "installed and enabled" -- every enabled
                # provider participates in the composition simultaneously
                # (`build_provider_composition`), so there is no single
                # provider_id left to compare against.
                row["active"] = installed is not None and installed.enabled
            rows.append(row)
        return {"ok": True, "providers": rows}

    def install_provider_package(self, *, entry_point_name: str | None, config: dict | None) -> dict:
        """Activate a discovered provider package with household-supplied config.

        Building the real instance here (not deferred to the next boot) is
        deliberate: a bad credential or an unreachable device fails this
        call immediately, with the household's prior provider (if any)
        left untouched, rather than being discovered only after a restart
        already switched over.
        """

        if not isinstance(entry_point_name, str) or not entry_point_name.strip():
            return {"ok": False, "error": "a non-empty 'entry_point_name' is required"}
        entry_point_name = entry_point_name.strip()
        from haven.providers.loader import ProviderLoadError, build_provider, discover_provider_packages

        discovered = next(
            (d for d in discover_provider_packages() if d.entry_point_name == entry_point_name), None
        )
        if discovered is None:
            return {"ok": False, "error": f"no installed package declares the entry point {entry_point_name!r}"}
        try:
            manifest, _instance = build_provider(discovered, config=config or {})
        except ProviderLoadError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"could not activate {entry_point_name!r}: {exc}"}
        secret_fields = frozenset(field.name for field in manifest.config_fields if field.secret)
        save_installed_provider(
            self._store,
            provider_id=manifest.provider_id,
            entry_point_name=entry_point_name,
            config=config or {},
            secret_fields=secret_fields,
        )
        # Deliberately never touches `self._config`/`provider_kind`: that
        # field is Home Assistant's own connect step, not "the" active
        # provider (see `is_real_installation`'s docstring) -- installing
        # a second provider must never look like it silently replaced the
        # first. `installed_providers.json` (just written above) is this
        # provider's own durable "configured" record.
        self._trigger_rebuild()
        return {"ok": True, "provider_id": manifest.provider_id}

    def set_provider_package_enabled(self, *, provider_id: str | None, enabled: bool) -> dict:
        if not isinstance(provider_id, str) or not provider_id.strip():
            return {"ok": False, "error": "a non-empty 'provider_id' is required"}
        provider_id = provider_id.strip()
        if find_installed_provider(self._store, provider_id) is None:
            return {"ok": False, "error": f"no installed provider {provider_id!r}"}
        set_installed_provider_enabled(self._store, provider_id, enabled)
        self._trigger_rebuild()
        return {"ok": True}

    def uninstall_provider_package(self, *, provider_id: str | None) -> dict:
        """Deactivate a provider package without touching `provider_kind`.

        A household configured for this provider stays configured for it --
        `provider_kind` names what the household *wants*, not whether a
        package for it happens to be installed right now. Clearing it here
        would resurface the demo fixture on the next rebuild for a household
        with real enrolled devices and declared people, exactly the silent
        fallback item 3 of this wave removed. With the package gone, the
        household simply gets the same honest "no live evidence" world any
        other unreachable provider already degrades to.
        """

        if not isinstance(provider_id, str) or not provider_id.strip():
            return {"ok": False, "error": "a non-empty 'provider_id' is required"}
        remove_installed_provider(self._store, provider_id.strip())
        self._trigger_rebuild()
        return {"ok": True}

    def run_discovery(self) -> dict:
        """Scan for enrollment candidates: real HA entities, plus the demo
        fixture set only when this installation is an actual demo run.

        When a Home Assistant state source is attached, its entities join the
        candidate list. A fetch failure never breaks the scan: an unreachable
        provider at scan time yields whatever candidates don't depend on it
        (the demo set, in a demo run; nothing, in a real one), never a crash.
        """

        now = self._clock()
        enrolled_ids = set(self._enrolled)
        discovered = [self._discover(demo, now) for demo in _DEMO_SCAN_CANDIDATES] if self._include_demo_candidates else []
        if self._ha_states_source is not None:
            try:
                states = self._ha_states_source.fetch_states()
            except Exception:
                states = ()
            discovered.extend(ha_candidates_from_states(states, now=now))
        candidates = []
        for item in discovered:
            candidates.append(
                SetupCandidate(
                    candidate_id=item.candidate_id,
                    provider_id=item.provider_id,
                    source=item.source,
                    suggested_device_type=item.suggested_device_type,
                    suggested_room=item.suggested_room,
                    signal_strength=item.signal_strength,
                    discovered_at=item.discovered_at.isoformat(),
                    enrolled=item.candidate_id in enrolled_ids,
                )
            )
        self._last_scan = tuple(candidates)
        return {"ok": True, "candidates": [asdict(candidate) for candidate in self._last_scan]}

    def enroll(self, candidate_id: str, *, device_type: str, room: str | None = None) -> dict:
        if not self._director.has_declared_owner:
            # `approved_by` must name a real person: a real household with
            # nobody declared yet has no one to attribute enrollment to, and
            # falling back to a fixture identity is exactly the silent
            # gerron-as-default this refuses.
            return {"ok": False, "error": "declare a household owner before enrolling devices"}
        candidate = self._lookup_candidate(candidate_id)
        if candidate is None:
            return {"ok": False, "error": f"unknown candidate: {candidate_id!r}"}
        if candidate_id in self._enrolled:
            return {"ok": False, "error": f"candidate is already enrolled: {candidate_id}"}
        capabilities = _CAPABILITY_PRESETS.get(device_type)
        if capabilities is None:
            return {"ok": False, "error": f"unsupported device_type: {device_type!r}"}
        manifest = enroll_device(
            candidate,
            device_type=device_type,
            capabilities=capabilities,
            approved_by=self._director.owner.actor_id,
            justification=_ENROLL_JUSTIFICATION,
            room=room if room else candidate.suggested_room,
            semantic_role=device_type,
        )
        self._director.registry.register(manifest)
        self._enrolled[candidate_id] = {
            "candidate_id": candidate_id,
            "device_id": manifest.device_id,
            "device_type": manifest.device_type,
            "room": manifest.room,
        }
        error = self._persist_enrolled()
        if error is not None:
            return {"ok": False, "error": error}
        return self.status()

    def set_preferences(self, *, voice: bool, intelligence: bool) -> dict:
        if not isinstance(voice, bool) or not isinstance(intelligence, bool):
            return {"ok": False, "error": "'voice' and 'intelligence' must be booleans"}
        self._config = replace(self._config, voice_enabled=voice, intelligence_enabled=intelligence)
        error = self._save()
        if error is not None:
            return {"ok": False, "error": error}
        return self.status()

    def complete(self) -> dict:
        # A real installation means real actions are possible once the
        # wizard closes; a real household (`is_real_installation`: a
        # persisted household_id, Home Assistant connected, or any
        # community provider ever installed) must declare a real owner
        # before that happens, or every governed action would run as
        # whatever fixture identity `DemoDirector` falls back to. A pure
        # demo run (nothing ever configured) has no such requirement -- its
        # fixture identity is the point, not a gap.
        if is_real_installation(
            self._store,
            household_id=self._config.household_id,
            home_assistant_base_url=self._config.provider_base_url,
            computer_provider_enabled=load_computer_provider_config(self._store).enabled,
        ) and not any(person.role == "owner" for person in self.household.people):
            return {"ok": False, "error": "declare a household owner before finishing setup"}
        self._config = replace(self._config, completed=True)
        error = self._save()
        if error is not None:
            return {"ok": False, "error": error}
        return self.status()

    def reopen(self) -> dict:
        self._config = replace(self._config, completed=False)
        error = self._save()
        if error is not None:
            return {"ok": False, "error": error}
        return self.status()

    # -- computer/filesystem provider --------------------------------------

    def _computer_provider_payload(self) -> dict:
        config = load_computer_provider_config(self._store)
        return {"enabled": config.enabled, "allowed_roots": list(config.allowed_roots), "read_only": config.read_only}

    def set_computer_provider_enabled(self, *, enabled: bool, read_only: bool | None = None) -> dict:
        if not isinstance(enabled, bool):
            return {"ok": False, "error": "'enabled' must be a boolean"}
        current = load_computer_provider_config(self._store)
        if enabled and not current.allowed_roots:
            return {"ok": False, "error": "add at least one allowed folder before enabling computer access"}
        updated = replace(current, enabled=enabled, read_only=current.read_only if read_only is None else read_only)
        save_computer_provider_config(self._store, updated)
        self._trigger_rebuild()
        return self.status()

    def add_computer_provider_root(self, *, path: str | None) -> dict:
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "a non-empty 'path' is required"}
        candidate = Path(path.strip())
        if not candidate.is_dir():
            return {"ok": False, "error": f"not a folder this machine can see: {candidate}"}
        resolved = str(candidate.resolve())
        current = load_computer_provider_config(self._store)
        if resolved in current.allowed_roots:
            return self.status()
        updated = replace(current, allowed_roots=current.allowed_roots + (resolved,))
        save_computer_provider_config(self._store, updated)
        self._trigger_rebuild()
        return self.status()

    def remove_computer_provider_root(self, *, path: str | None) -> dict:
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "a non-empty 'path' is required"}
        try:
            normalized_path = str(Path(path.strip()).expanduser().resolve())
        except OSError:
            normalized_path = path.strip()
        current = load_computer_provider_config(self._store)
        remaining = tuple(root for root in current.allowed_roots if root != normalized_path)
        # Disabling automatically once nothing is left to read is the honest
        # move here, not an error: a household removing its last folder
        # clearly means "stop", not "keep scanning nothing".
        updated = replace(current, allowed_roots=remaining, enabled=current.enabled and bool(remaining))
        save_computer_provider_config(self._store, updated)
        if self._knowledge_service is not None:
            self._knowledge_service.revoke_locator_prefix(normalized_path.rstrip("/\\"))
        elif self._resource_store is not None:
            # Revoking a folder must hide its indexed contents -- the folder
            # resource itself and everything under it -- from search
            # immediately, not only once the next scan happens to reconcile
            # them.
            self._resource_store.mark_stale_by_locator_prefix(normalized_path.rstrip("/\\"))
        self._trigger_rebuild()
        return self.status()

    def scan_computer_provider(self) -> dict:
        """Observe every allowed folder right now and persist what it finds
        into the resource store this installation's search bar reads from.

        Deliberately synchronous and on-demand -- the same one-shot,
        caller-controls-the-cadence shape `HomeAssistantObserver.observe()`
        already uses -- rather than a background poller this module would
        have to manage the lifecycle of. Reconciling against the store after
        saving is what turns a file deleted from disk, or a folder no longer
        reachable, into a stale record instead of a stale record's opposite:
        one that silently keeps looking current forever.
        """

        config = load_computer_provider_config(self._store)
        provider = build_filesystem_provider(config, scope_id=self._director.household_id)
        if provider is None:
            return {"ok": False, "error": "computer access is not enabled, or no allowed folder is reachable"}
        if self._resource_store is None:
            return {"ok": False, "error": "no resource store is attached to this installation"}
        records = provider.observe()
        for record in records:
            if self._knowledge_service is not None:
                # The knowledge service compares against the previous
                # resource row, so ingest happens before this scan's upsert.
                self._knowledge_service.ingest_resource(record, reader=provider.read_text)
            self._resource_store.save(record)
        staled = self._resource_store.reconcile(
            provider_id=provider.provider_id,
            scope_id=self._director.household_id,
            observed_ids=(record.resource_id for record in records),
        )
        claims_staled = 0
        if self._knowledge_service is not None:
            claims_staled = self._knowledge_service.reconcile_stale_sources(
                provider_id=provider.provider_id,
                scope_id=self._director.household_id,
            )
        return {"ok": True, "scanned": len(records), "staled": staled, "claims_staled": claims_staled}

    def declare_person(
        self, *, name: str, entity_id: str | None = None, room_id: str | None = None, role: str = "member"
    ) -> dict:
        """Declare one person, optionally with the occupancy entity that reports them in a room.

        A person can be declared with just a name and role -- HAVEN needs to
        know who owns this installation before it needs to know how presence
        is sensed; a presence source can be added in the same call or a
        later one. `entity_id` and `room_id` are a pair: give both or
        neither.

        The person_id is derived from the name ("Gerron Smith" -> "gerron_smith").
        Re-declaring the same (person_id, entity_id) pair is idempotent; the
        same person_id under a different name is a conflict, because the
        declaration says who the person is, not just how to spell the id.
        Re-declaring the same person under a different role updates the role.
        """

        try:
            name = _require_text(name, name="name")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        entity_id = entity_id.strip() if isinstance(entity_id, str) else ""
        room_id = room_id.strip() if isinstance(room_id, str) else ""
        if bool(entity_id) != bool(room_id):
            return {"ok": False, "error": "entity_id and room_id must be given together"}
        if role not in _DECLARED_ROLES:
            return {"ok": False, "error": f"role must be one of {_DECLARED_ROLES}, got {role!r}"}
        person_id = _derived_declared_id(name)
        if not person_id:
            return {"ok": False, "error": "name must contain at least one letter or digit"}
        people = list(self.household.people)
        for index, person in enumerate(people):
            if person.person_id != person_id:
                continue
            if person.name != name:
                return {"ok": False, "error": f"person already declared with a different name: {person_id}"}
            if person.role != role:
                people[index] = replace(person, role=role)
                self.household = replace(self.household, people=tuple(people))
                error = self._persist_household()
                if error is not None:
                    return {"ok": False, "error": error}
                self._trigger_rebuild()
                return self.status()
            if not entity_id or any(source.entity_id == entity_id for source in person.sources):
                # No new source given, or this one is already declared:
                # re-declaring an already-known person is a harmless no-op.
                return self.status()
            people[index] = replace(
                person,
                sources=person.sources + (DeclaredPresenceSource(entity_id=entity_id, room_id=room_id),),
            )
            self.household = replace(self.household, people=tuple(people))
            error = self._persist_household()
            if error is not None:
                return {"ok": False, "error": error}
            self._trigger_rebuild()
            return self.status()
        new_sources = (DeclaredPresenceSource(entity_id=entity_id, room_id=room_id),) if entity_id else ()
        people.append(DeclaredPerson(person_id=person_id, name=name, sources=new_sources, role=role))
        self.household = replace(self.household, people=tuple(people))
        error = self._persist_household()
        if error is not None:
            return {"ok": False, "error": error}
        self._trigger_rebuild()
        return self.status()

    def remove_person(self, *, person_id: str) -> dict:
        try:
            person_id = _require_text(person_id, name="person_id")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        remaining = tuple(person for person in self.household.people if person.person_id != person_id)
        if len(remaining) == len(self.household.people):
            return {"ok": False, "error": f"unknown person: {person_id}"}
        self.household = replace(self.household, people=remaining)
        error = self._persist_household()
        if error is not None:
            return {"ok": False, "error": error}
        self._trigger_rebuild()
        return self.status()

    def declare_context(self, *, label: str, entity_id: str) -> dict:
        """Declare what one entity means: its "on" activates the context.

        The context_id is derived from the label like a person_id from a name.
        Contexts are meaning, not wiring, so several contexts may share one
        entity; the idempotency/conflict rules are the people's.
        """

        try:
            label = _require_text(label, name="label")
            entity_id = _require_text(entity_id, name="entity_id")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        context_id = _derived_declared_id(label)
        if not context_id:
            return {"ok": False, "error": "label must contain at least one letter or digit"}
        contexts = list(self.household.contexts)
        for index, context in enumerate(contexts):
            if context.context_id != context_id:
                continue
            if context.label != label:
                return {"ok": False, "error": f"context already declared with a different label: {context_id}"}
            if context.entity_id == entity_id:
                return self.status()
            contexts[index] = replace(context, entity_id=entity_id)
            self.household = replace(self.household, contexts=tuple(contexts))
            error = self._persist_household()
            if error is not None:
                return {"ok": False, "error": error}
            self._trigger_rebuild()
            return self.status()
        contexts.append(DeclaredContext(context_id=context_id, label=label, entity_id=entity_id))
        self.household = replace(self.household, contexts=tuple(contexts))
        error = self._persist_household()
        if error is not None:
            return {"ok": False, "error": error}
        self._trigger_rebuild()
        return self.status()

    def remove_context(self, *, context_id: str) -> dict:
        try:
            context_id = _require_text(context_id, name="context_id")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        remaining = tuple(context for context in self.household.contexts if context.context_id != context_id)
        if len(remaining) == len(self.household.contexts):
            return {"ok": False, "error": f"unknown context: {context_id}"}
        self.household = replace(self.household, contexts=remaining)
        error = self._persist_household()
        if error is not None:
            return {"ok": False, "error": error}
        self._trigger_rebuild()
        return self.status()

    def _config_dir(self) -> Path:
        return self._store.path.parent

    def _load_config(self) -> SetupConfig:
        try:
            return self._store.load()
        except SetupConfigError as exc:
            # A broken config must not take the whole surface down: run on the
            # defaults and surface the load error in status until a step saves.
            self._config_error = str(exc)
            return SetupConfig()

    def _save(self) -> str | None:
        try:
            self._store.save(self._config)
        except SetupConfigError as exc:
            return str(exc)
        self._config_error = None
        return None

    def _enrolled_path(self) -> Path:
        # Derived from store.path.parent at call time: choosing a new data
        # dir rebinds the store, and the sidecar must move with it.
        return self._config_dir() / _ENROLLED_FILENAME

    def _load_enrolled(self) -> dict[str, dict]:
        load = load_enrolled_sidecar(self._enrolled_path())
        for manifest in load.manifests:
            # Re-register every persisted manifest: a restart must not forget
            # the runtime the enrollment decided on.
            self._director.registry.register(manifest)
        if load.migrated:
            try:
                _write_json_atomic(self._enrolled_path(), _enrolled_v2_payload(load.manifests))
            except SetupConfigError:
                # The eager rewrite is best-effort at boot; the migration
                # simply runs again on the next load.
                pass
        return load.rows

    def _persist_enrolled(self) -> str | None:
        manifests = []
        for row in self._enrolled.values():
            device_id = row["device_id"]
            if self._director.registry.is_registered(device_id):
                manifests.append(self._director.registry.get(device_id).to_dict())
        try:
            _write_json_atomic(
                self._enrolled_path(),
                {"version": _ENROLLED_VERSION, "manifests": manifests},
            )
        except SetupConfigError as exc:
            return str(exc)
        return None

    def _household_path(self) -> Path:
        # Derived from store.path.parent at call time, like the enrolled
        # sidecar: choosing a new data dir moves the whole installation.
        return self._config_dir() / _HOUSEHOLD_FILENAME

    def _load_household(self) -> HouseholdDeclarations:
        try:
            return load_household_declarations(self._household_path())
        except SetupConfigError:
            # Mirror the enrolled sidecar's laziness: a bad household file
            # degrades to "nobody declared", it does not prevent boot.
            return HouseholdDeclarations()

    def _persist_household(self) -> str | None:
        payload = {"version": _HOUSEHOLD_VERSION, **_household_payload(self.household)}
        try:
            _write_json_atomic(self._household_path(), payload)
        except SetupConfigError as exc:
            return str(exc)
        return None

    def _lookup_candidate(self, candidate_id: str) -> DiscoveredDevice | None:
        demo = next((item for item in _DEMO_SCAN_CANDIDATES if item.candidate_id == candidate_id), None)
        if demo is not None:
            return self._discover(demo, self._clock())
        if self._ha_states_source is None:
            return None
        try:
            states = self._ha_states_source.fetch_states()
        except Exception:
            # An entity that cannot be confirmed live right now is not
            # enrollable: refuse as unknown rather than enroll blind.
            return None
        return next(
            (
                item
                for item in ha_candidates_from_states(states, now=self._clock())
                if item.candidate_id == candidate_id
            ),
            None,
        )

    @staticmethod
    def _discover(demo: _DemoScanCandidate, now: datetime) -> DiscoveredDevice:
        return DiscoveredDevice(
            candidate_id=demo.candidate_id,
            provider_id=demo.source,
            discovered_at=now,
            source=demo.source,
            suggested_device_type=demo.suggested_device_type,
            suggested_room=demo.suggested_room,
            signal_strength=demo.signal_strength,
        )


__all__ = [
    "DeclaredContext",
    "DeclaredPerson",
    "DeclaredPresenceSource",
    "HouseholdDeclarations",
    "SetupCandidate",
    "SetupService",
    "ha_candidates_from_states",
    "load_enrolled_sidecar",
    "load_household_declarations",
]
