"""A camera manifest: hardware capabilities, not a live connection.

A provider (RTSP, ONVIF, Home Assistant, Frigate, a vendor plugin) is what
actually knows how to reach a camera; this module only describes what one
exposes, the same way `haven.devices.DeviceManifest` describes an actuator
without knowing how to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class CameraCapabilities:
    """The hardware feature set a camera declares -- flat and boolean.

    Unlike `haven.devices.CapabilityDescriptor`, there is no control class or
    routed service here: these say what hardware exists (does this camera
    have a microphone, a privacy shutter, a PTZ motor), not how to actuate
    it. Actuating PTZ, muting a microphone, or engaging a privacy shutter is
    a capability-routing concern for later, the same way `haven.devices`
    separated "device exists with a `power` capability" from "here is the
    service that turns it off."
    """

    live_stream: bool = False
    recording: bool = False
    ptz: bool = False
    optical_zoom: bool = False
    microphone: bool = False
    speaker: bool = False
    infrared_mode: bool = False
    privacy_shutter: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "live_stream": self.live_stream,
            "recording": self.recording,
            "ptz": self.ptz,
            "optical_zoom": self.optical_zoom,
            "microphone": self.microphone,
            "speaker": self.speaker,
            "infrared_mode": self.infrared_mode,
            "privacy_shutter": self.privacy_shutter,
        }

    def has(self, capability: str) -> bool:
        try:
            return self.as_dict()[capability]
        except KeyError:
            raise ValueError(f"unknown camera capability: {capability!r}") from None


@dataclass(frozen=True)
class CameraManifest:
    """One discovered or registered camera, independent of its provider."""

    camera_id: str
    provider_id: str
    capabilities: CameraCapabilities
    room: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "camera_id", _require_text(self.camera_id, name="camera_id"))
        object.__setattr__(self, "provider_id", _require_text(self.provider_id, name="provider_id"))
        if not isinstance(self.capabilities, CameraCapabilities):
            raise ValueError("capabilities must be a CameraCapabilities value")
        if self.room is not None:
            object.__setattr__(self, "room", _require_text(self.room, name="room"))


__all__ = ["CameraCapabilities", "CameraManifest"]
