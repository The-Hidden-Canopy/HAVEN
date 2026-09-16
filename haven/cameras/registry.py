"""Discovery for registered camera manifests.

Mirrors `haven.devices.DeviceRegistry` exactly: register by camera_id, look
up by camera_id, filter by room or by required hardware capabilities. This
registry does not discover anything itself -- a provider (RTSP, ONVIF, an
NVR's API) finds cameras on the network and registers what it finds here.
"""

from __future__ import annotations

from typing import Iterable

from .manifest import CameraManifest


class UnknownCamera(KeyError):
    """Raised when a camera_id has not been registered."""


class CameraRegistry:
    def __init__(self) -> None:
        self._cameras: dict[str, CameraManifest] = {}

    def register(self, manifest: CameraManifest) -> None:
        self._cameras[manifest.camera_id] = manifest

    def is_registered(self, camera_id: str) -> bool:
        return camera_id in self._cameras

    def get(self, camera_id: str) -> CameraManifest:
        try:
            return self._cameras[camera_id]
        except KeyError:
            raise UnknownCamera(camera_id) from None

    def find(self, *, room: str | None = None, requires: Iterable[str] = ()) -> tuple[str, ...]:
        """Return camera_ids matching an optional room and every required capability."""

        required = tuple(requires)
        return tuple(
            camera_id
            for camera_id, manifest in self._cameras.items()
            if (room is None or manifest.room == room)
            and all(manifest.capabilities.has(capability) for capability in required)
        )

    def all_cameras(self) -> tuple[CameraManifest, ...]:
        return tuple(self._cameras.values())


__all__ = ["CameraRegistry", "UnknownCamera"]
