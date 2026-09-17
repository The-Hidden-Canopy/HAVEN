"""A discovered candidate device: what a scan can know, and no more."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class DiscoveredDevice:
    """A transport's best guess at one device, before any household decision.

    `suggested_device_type` and `suggested_room` are guesses (from a BLE
    advertised name, an mDNS service type, a vendor's own model string) --
    not the validated `device_type`/`room` a `DeviceManifest` carries.
    `signal_strength` is whatever proximity/quality signal the transport
    has (RSSI for BLE, a WiFi association's own signal, or `None` for a
    transport with no such concept); it is informational only here, though
    a caller MAY fold it into a `PresenceState.confidence` if it is later
    used as a perception source, not a control source.
    """

    candidate_id: str
    provider_id: str
    discovered_at: datetime
    source: str
    suggested_device_type: str | None = None
    suggested_room: str | None = None
    signal_strength: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, name="candidate_id"))
        object.__setattr__(self, "provider_id", _require_text(self.provider_id, name="provider_id"))
        object.__setattr__(self, "discovered_at", require_aware_utc(self.discovered_at, name="discovered_at"))
        object.__setattr__(self, "source", _require_text(self.source, name="source"))
        if self.suggested_device_type is not None:
            object.__setattr__(
                self, "suggested_device_type", _require_text(self.suggested_device_type, name="suggested_device_type")
            )
        if self.suggested_room is not None:
            object.__setattr__(self, "suggested_room", _require_text(self.suggested_room, name="suggested_room"))


__all__ = ["DiscoveredDevice"]
