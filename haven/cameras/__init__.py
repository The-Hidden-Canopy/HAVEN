"""Camera discovery, and the bridge into actuation.

Mirrors `haven.devices`: a manifest is purely descriptive, and nothing here
performs real network discovery (ONVIF WS-Discovery, RTSP probing, an NVR's
own API) -- that is a provider's job. A provider that finds a camera on the
network registers a `CameraManifest` here; Haven never assumes a camera
exists that nothing has registered.

`camera_to_device_manifest()` is what makes a discovered camera controllable:
it turns declared PTZ/zoom/privacy-shutter hardware into ordinary
`haven.devices.CapabilityDescriptor`s, so a camera runs through the exact
same `AuthorityEngine` + `ExecutionProviderRegistry` pipeline as any other
device. Live view, stream health, presets, recording policy, retention, and
snapshots are still not part of this module.
"""

from .actuation import camera_to_device_manifest
from .manifest import CameraCapabilities, CameraManifest
from .registry import CameraRegistry, UnknownCamera

__all__ = [
    "CameraCapabilities",
    "CameraManifest",
    "CameraRegistry",
    "UnknownCamera",
    "camera_to_device_manifest",
]
