"""The first-run onboarding workflow behind `/api/setup`.

The demo world stays untouched: these steps persist HAVEN's own installation
config (data dir, provider, preferences) and enroll demo discovery candidates
into the director's device registry. A real BLE/mDNS scan is a future native
seam; the scan here returns honestly labeled demo candidates in the shape a
real transport would produce.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..devices import CapabilityDescriptor, ControlClass
from ..discovery.enrollment import enroll_device
from ..discovery.models import DiscoveredDevice
from ..integrations.home_assistant.client import LiveHomeAssistantAdapter
from .setup_config import (
    SetupConfig,
    SetupConfigError,
    SetupConfigStore,
    _write_json_atomic,
    default_data_dir,
)

_ENROLL_JUSTIFICATION = "enrolled from the setup wizard discovery scan"
_TOKEN_FILENAME = "ha_token.txt"
_ENROLLED_FILENAME = "enrolled_devices.json"

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
    ) -> None:
        self._store = store
        self._director = director
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._config_error: str | None = None
        self._config = self._load_config()
        self._enrolled: dict[str, dict] = self._load_enrolled()
        self._last_scan: tuple[SetupCandidate, ...] | None = None

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
                    "configured": config.provider_kind is not None,
                    "kind": config.provider_kind,
                    "base_url": config.provider_base_url,
                },
                "discovery": {
                    "candidates": [asdict(candidate) for candidate in self._last_scan]
                    if self._last_scan is not None
                    else [],
                    "enrolled": list(self._enrolled.values()),
                },
                "preferences": {
                    "voice": config.voice_enabled,
                    "intelligence": config.intelligence_enabled,
                },
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
        self._config = replace(self._config, data_dir=str(resolved))
        error = self._save()
        if error is not None:
            return {"ok": False, "error": error}
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
            self._config = replace(
                self._config,
                provider_kind=None,
                provider_base_url=None,
                provider_token_file=None,
            )
            error = self._save()
            if error is not None:
                return {"ok": False, "error": error}
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
        return self.status()

    def run_discovery(self) -> dict:
        now = self._clock()
        enrolled_ids = set(self._enrolled)
        candidates = []
        for demo in _DEMO_SCAN_CANDIDATES:
            discovered = self._discover(demo, now)
            candidates.append(
                SetupCandidate(
                    candidate_id=discovered.candidate_id,
                    provider_id=discovered.provider_id,
                    source=discovered.source,
                    suggested_device_type=discovered.suggested_device_type,
                    suggested_room=discovered.suggested_room,
                    signal_strength=discovered.signal_strength,
                    discovered_at=discovered.discovered_at.isoformat(),
                    enrolled=discovered.candidate_id in enrolled_ids,
                )
            )
        self._last_scan = tuple(candidates)
        return {"ok": True, "candidates": [asdict(candidate) for candidate in self._last_scan]}

    def enroll(self, candidate_id: str, *, device_type: str, room: str | None = None) -> dict:
        demo = next((item for item in _DEMO_SCAN_CANDIDATES if item.candidate_id == candidate_id), None)
        if demo is None:
            return {"ok": False, "error": f"unknown candidate: {candidate_id!r}"}
        if candidate_id in self._enrolled:
            return {"ok": False, "error": f"candidate is already enrolled: {candidate_id}"}
        capabilities = _CAPABILITY_PRESETS.get(device_type)
        if capabilities is None:
            return {"ok": False, "error": f"unsupported device_type: {device_type!r}"}
        manifest = enroll_device(
            self._discover(demo, self._clock()),
            device_type=device_type,
            capabilities=capabilities,
            approved_by=self._director.owner.actor_id,
            justification=_ENROLL_JUSTIFICATION,
            room=room if room else demo.suggested_room,
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
        return self._config_dir() / _ENROLLED_FILENAME

    def _load_enrolled(self) -> dict[str, dict]:
        try:
            raw = self._enrolled_path().read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {}
        entries = payload.get("enrolled") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return {}
        enrolled: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("candidate_id"), str):
                continue
            enrolled[entry["candidate_id"]] = {
                "candidate_id": entry["candidate_id"],
                "device_id": entry.get("device_id"),
                "device_type": entry.get("device_type"),
                "room": entry.get("room"),
            }
        return enrolled

    def _persist_enrolled(self) -> str | None:
        try:
            _write_json_atomic(self._enrolled_path(), {"enrolled": list(self._enrolled.values())})
        except SetupConfigError as exc:
            return str(exc)
        return None

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


__all__ = ["SetupCandidate", "SetupService"]
