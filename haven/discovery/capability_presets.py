"""Device-type -> capability template presets, shared by every enrollment path.

A discovered candidate's `suggested_device_type` only ever proposes a capability
*template*; the household still reviews the resulting manifest (spec: "protocol
metadata can propose a safe capability template, but templates are suggestions").
Both the setup wizard's discovery step and the everyday `DiscoveryService` enroll
through this same preset table so there is exactly one place that decides what
capabilities a device type is allowed to claim -- never a second, forked mapping.
"""

from __future__ import annotations

from ..devices import CapabilityDescriptor, ControlClass

CAPABILITY_PRESETS: dict[str, tuple[CapabilityDescriptor, ...]] = {
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
    # zoneplayer/media: bluetooth and SSDP candidates commonly suggest this
    # (Sonos-style UPnP ZonePlayer, BLE speaker advertisements). Observation
    # only for the same reason as camera: no verified command path exists
    # from a bare discovery candidate alone.
    "zoneplayer": (CapabilityDescriptor("live_state", ControlClass.READ, readable=True),),
}

__all__ = ["CAPABILITY_PRESETS"]
