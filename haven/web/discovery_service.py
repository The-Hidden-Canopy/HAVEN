"""DiscoveryService: everyday (post-setup) device discovery over real transports.

Distinct from the setup wizard's one-time `SetupService.run_discovery()`
(which mixes in demo fixtures for onboarding): this is the ongoing "scan for
new devices" surface a household reaches after setup, real transports only,
no demo candidates. A transport failing (no adapter, no library, a timed-out
socket) never blanks the others -- each provider's `discover()` is isolated.

Enrollment reuses `haven.discovery.enroll_device()` unchanged: a scan
suggestion (`suggested_device_type`) is a UI hint, never a decision, and the
capability set actually granted still comes from the explicit
`CAPABILITY_PRESETS` table the setup wizard's own enrollment shares (spec:
"protocol metadata can propose a safe capability template, but templates are
suggestions" -- see `haven.discovery.capability_presets`'s docstring). The
post-setup surface does not invent a second enrollment record:
`enroll_device()` always sets `device_id=candidate_id`, and the composed
server writes the resulting manifest through `SetupService` into the same
`enrolled_devices.json` sidecar the setup wizard uses. "Is this candidate
already enrolled" is still answered from the shared `DeviceRegistry` during
the run, while the sidecar supplies restart durability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from haven.core.domain import EvidenceStatus
from haven.devices import DeviceManifest
from haven.discovery import DiscoveredDevice, DiscoveryProvider, correlate, enroll_device
from haven.discovery.capability_presets import CAPABILITY_PRESETS
from haven.integrations.wifi import MdnsDiscoveryProvider, SsdpDiscoveryProvider

_ENROLL_JUSTIFICATION = "enrolled from the Discover scan"


def default_discovery_providers() -> tuple[DiscoveryProvider, ...]:
    """The real transports this installation can actually scan with.

    SSDP and mDNS/DNS-SD are both stdlib-only and always available. Bluetooth is included only
    when a HAVEN-BT native library (`havenbt.dll`/`.so`/`.dylib`) is
    actually loadable via `ctypes.util.find_library("havenbt")` -- on
    Windows a real, hardware-verified WinRT backend exists in
    `native/haven-bt/src/platform/windows/winrt_backend.cpp` (see
    `native/haven-bt/README.md`), but nothing in the current build/install
    pipeline compiles it and places the resulting DLL somewhere this lookup
    finds it, so on a normal installed machine this still degrades to "one
    fewer transport", never a crash or a fabricated capability. Linux/BlueZ
    and macOS/CoreBluetooth backends are not built at all.
    """

    providers: list[DiscoveryProvider] = [SsdpDiscoveryProvider(), MdnsDiscoveryProvider()]
    try:
        from haven.integrations.bluetooth import BluetoothProvider, CtypesBluetoothLibrary

        library = CtypesBluetoothLibrary.load()
    except OSError:
        pass
    else:
        providers.append(BluetoothProvider(library))
    return tuple(providers)


class DiscoveryService:
    """One façade over every real discovery transport; enrollment is governed."""

    def __init__(
        self,
        *,
        director,
        providers: tuple[DiscoveryProvider, ...],
        persist_enrollment: Callable[[DeviceManifest], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        verification_reader: Callable[[str], dict] | None = None,
        record_verification: Callable[[str, dict], str | None] | None = None,
    ) -> None:
        self._director = director
        self._providers = providers
        self._persist_enrollment = persist_enrollment
        director_clock = getattr(director, "_clock", None)
        self._clock = clock or (director_clock if callable(director_clock) else lambda: datetime.now(timezone.utc))
        self._verification_reader = verification_reader
        self._record_verification = record_verification
        self._verification: dict[str, dict] = {}
        self._last_scan: tuple[DiscoveredDevice, ...] = ()

    def set_director(self, director) -> None:
        """Rebind to a freshly built director after the composition root rebuilds it.

        Called by `HavenWebServer.rebuild_director` immediately after it
        swaps the live director (a data-dir move, a demo reset), the same
        way `SetupService.set_director` does -- otherwise enrollment would
        keep registering into a director instance nothing else still uses.
        """

        self._director = director

    def scan(self) -> dict:
        found: dict[str, DiscoveredDevice] = {}
        for provider in self._providers:
            try:
                candidates = provider.discover()
            except Exception:
                # One transport's failure (no adapter present, a socket
                # error, a permission problem) never blanks the others.
                continue
            for candidate in candidates:
                found[candidate.candidate_id] = candidate
        self._last_scan = tuple(found.values())
        return self.candidates()

    def candidates(self) -> dict:
        """The most recent scan's results, without re-scanning."""

        groups = correlate(self._last_scan)
        return {"ok": True, "candidates": [self._view(candidate, groups) for candidate in self._last_scan]}

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
        if self._director.registry.is_registered(candidate_id):
            return {"ok": False, "error": f"candidate is already enrolled: {candidate_id}"}
        capabilities = CAPABILITY_PRESETS.get(device_type)
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
        if self._persist_enrollment is not None:
            error = self._persist_enrollment(manifest)
            if error is not None:
                return {"ok": False, "error": error}
        self._director.registry.register(manifest)
        return self.candidates()

    def verify(self, candidate_id: str) -> dict:
        """Perform a harmless provider read and retain its evidence boundary."""

        if not self._director.registry.is_registered(candidate_id):
            return {"ok": False, "error": f"candidate is not enrolled: {candidate_id}"}
        manifest = self._director.registry.get(candidate_id)
        checked_at = self._clock()
        verification = self._unavailable_verification(
            checked_at,
            detail="No current provider state was returned for this enrolled device.",
        )
        try:
            snapshot = self._director.world.observe(checked_at)
        except Exception:
            # Provider failures are a truthful unavailable result, not a
            # reason to expose adapter internals or claim the binding works.
            verification = self._unavailable_verification(
                checked_at,
                detail="The provider could not return a harmless read for this device.",
            )
        else:
            state = next(
                (item for item in snapshot.devices if item.device_id == manifest.device_id),
                None,
            )
            if state is not None and state.status == EvidenceStatus.OBSERVED:
                verification = {
                    "status": "verified",
                    "verified": True,
                    "checked_at": checked_at.isoformat(),
                    "source": state.source,
                    "detail": "The provider returned an observed device state.",
                }
            elif state is not None and state.status == EvidenceStatus.UNAVAILABLE:
                verification = self._unavailable_verification(
                    checked_at,
                    source=state.source,
                    detail="The provider reported this device as unavailable.",
                )
            elif state is not None:
                verification = {
                    "status": "degraded",
                    "verified": False,
                    "checked_at": checked_at.isoformat(),
                    "source": state.source,
                    "detail": "The provider returned non-live evidence; the device binding was not verified.",
                }

        if self._record_verification is not None:
            error = self._record_verification(manifest.device_id, verification)
            if error is not None:
                return {"ok": False, "error": error}
        self._verification[manifest.device_id] = dict(verification)
        return {"ok": True, "device_id": manifest.device_id, "verification": verification, **self.candidates()}

    @staticmethod
    def _unavailable_verification(
        checked_at: datetime, *, source: str | None = None, detail: str
    ) -> dict:
        return {
            "status": "unavailable",
            "verified": False,
            "checked_at": checked_at.isoformat(),
            "source": source,
            "detail": detail,
        }

    def _lookup_candidate(self, candidate_id: str) -> DiscoveredDevice | None:
        return next((item for item in self._last_scan if item.candidate_id == candidate_id), None)

    def _view(self, candidate: DiscoveredDevice, groups: dict | None = None) -> dict:
        group = (groups if groups is not None else correlate(self._last_scan)).get(candidate.candidate_id)
        device_id = candidate.candidate_id
        if self._director.registry.is_registered(candidate.candidate_id):
            device_id = self._director.registry.get(candidate.candidate_id).device_id
        return {
            "candidate_id": candidate.candidate_id,
            "provider_id": candidate.provider_id,
            "source": candidate.source,
            "suggested_device_type": candidate.suggested_device_type,
            "suggested_room": candidate.suggested_room,
            "signal_strength": candidate.signal_strength,
            "discovered_at": candidate.discovered_at.isoformat(),
            # Which other candidate ids (if any) this scan believes name the
            # same physical device, per haven.discovery.correlation -- a
            # native Discover view can group these instead of rendering
            # unrelated-looking duplicate rows (spec §4.4). Evidence, not a
            # merge: every id here is still its own full candidate.
            "correlated_with": tuple(cid for cid in (group.candidate_ids if group else ()) if cid != candidate.candidate_id),
            "enrolled": self._director.registry.is_registered(candidate.candidate_id),
            "supported": candidate.suggested_device_type in CAPABILITY_PRESETS,
            "verification": self._read_verification(device_id),
        }

    def _read_verification(self, device_id: str) -> dict:
        if self._verification_reader is not None:
            try:
                value = self._verification_reader(device_id)
            except Exception:
                value = None
            if isinstance(value, dict):
                return dict(value)
        value = self._verification.get(device_id)
        if value is not None:
            return dict(value)
        return {
            "status": "unverified",
            "verified": False,
            "checked_at": None,
            "source": None,
            "detail": "No harmless read has been recorded.",
        }


__all__ = ["DiscoveryService", "default_discovery_providers"]
