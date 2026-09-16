"""Capability-shaped device modeling: what a device can do, not who made it.

Haven asks a device manifest whether a capability is readable, writable, and
what control class governs it -- never whether the device is a Samsung or an
LG. This package is purely descriptive: it does not execute commands (that
stays with a `haven.integrations` adapter). `AuthorityEngine` and
`HavenRuntime` read manifests through a `DeviceRegistry` for risk
classification and command routing; `DeviceSelector` (defined in
`haven.core.domain`, re-exported here) is what lets a rule name "every light
in the bedroom" instead of one `target_device_id`.
"""

from haven.core.domain import DeviceSelector

from .manifest import CapabilityDescriptor, ControlClass, DeviceManifest, UnknownCapability
from .registry import DeviceRegistry, UnknownDevice

__all__ = [
    "CapabilityDescriptor",
    "ControlClass",
    "DeviceManifest",
    "DeviceRegistry",
    "DeviceSelector",
    "UnknownCapability",
    "UnknownDevice",
]
