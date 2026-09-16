"""Camera discovery: what a camera is and what hardware capabilities it has.

Mirrors `haven.devices`: a manifest is purely descriptive, and nothing here
performs real network discovery (ONVIF WS-Discovery, RTSP probing, an NVR's
own API) -- that is a provider's job. A provider that finds a camera on the
network registers a `CameraManifest` here; Haven never assumes a camera
exists that nothing has registered.

Live view, stream health, PTZ *control* (as opposed to declaring that PTZ
hardware exists), presets, privacy zones, recording policy, retention, and
snapshots are not part of this module -- this is the "discover / add
cameras" piece only.
"""

from .manifest import CameraCapabilities, CameraManifest
from .registry import CameraRegistry, UnknownCamera

__all__ = ["CameraCapabilities", "CameraManifest", "CameraRegistry", "UnknownCamera"]
