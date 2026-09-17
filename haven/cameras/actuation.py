"""Bridge a discovered camera into an actuatable device.

`CameraManifest`/`CameraCapabilities` (this package) describe hardware; they
deliberately do not say how to move a PTZ motor or engage a privacy
shutter -- that is a `haven.devices` concern (control class + routed
service), the same one every other actuator already uses.
`camera_to_device_manifest()` is the one function that connects camera
discovery to the existing authority + execution pipeline: nothing new is
added to `AuthorityEngine` or `ExecutionProviderRegistry` for a camera to
become controllable, because a camera's PTZ/zoom/privacy-shutter
capabilities become ordinary `CapabilityDescriptor`s on a `DeviceManifest`,
addressed by the camera's own `camera_id` as `target_device_id`.

Recording start/stop, retention, and event-clip management are not part of
this bridge -- those need a storage/retention model this repo doesn't have
yet, unlike physical PTZ/zoom/shutter state, which is just another
authorized command.
"""

from __future__ import annotations

from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest

from .manifest import CameraManifest


def camera_to_device_manifest(
    camera: CameraManifest,
    *,
    pan_tilt_service: str | None = None,
    zoom_service: str | None = None,
    privacy_shutter_service: str | None = None,
) -> DeviceManifest:
    """Build the actuatable DeviceManifest for a camera's controllable hardware.

    Only capabilities the camera actually declares (`camera.capabilities`)
    are included, and each one requires its routing service -- the same
    requirement `CapabilityDescriptor` already enforces for every other
    writable capability. A camera declaring `ptz=True` with no
    `pan_tilt_service` raises rather than silently omitting PTZ or guessing
    a service name.

    Engaging/disengaging a privacy shutter is `ControlClass.GUARDED`, not
    `LOW_RISK` like pan/tilt/zoom: silently disabling the one hardware
    guarantee a household trusts for "this camera cannot see us right now"
    is exactly the failure mode a privacy shutter exists to prevent, so an
    automated rule cannot toggle it without confirmation.
    """

    capabilities: list[CapabilityDescriptor] = []
    if camera.capabilities.ptz:
        if not pan_tilt_service:
            raise ValueError(f"camera {camera.camera_id!r} declares ptz but no pan_tilt_service was given")
        capabilities.append(
            CapabilityDescriptor(
                name="pan_tilt",
                control_class=ControlClass.LOW_RISK,
                writable=True,
                values=("left", "right", "up", "down", "stop"),
                service=pan_tilt_service,
            )
        )
    if camera.capabilities.optical_zoom:
        if not zoom_service:
            raise ValueError(f"camera {camera.camera_id!r} declares optical_zoom but no zoom_service was given")
        capabilities.append(
            CapabilityDescriptor(
                name="zoom",
                control_class=ControlClass.LOW_RISK,
                writable=True,
                values=("in", "out", "stop"),
                service=zoom_service,
            )
        )
    if camera.capabilities.privacy_shutter:
        if not privacy_shutter_service:
            raise ValueError(
                f"camera {camera.camera_id!r} declares privacy_shutter but no privacy_shutter_service was given"
            )
        capabilities.append(
            CapabilityDescriptor(
                name="privacy_shutter",
                control_class=ControlClass.GUARDED,
                writable=True,
                values=("engage", "disengage"),
                service=privacy_shutter_service,
            )
        )

    if not capabilities:
        raise ValueError(
            f"camera {camera.camera_id!r} declares no controllable hardware "
            "(ptz, optical_zoom, or privacy_shutter) to build a DeviceManifest from"
        )

    return DeviceManifest(
        device_id=camera.camera_id,
        device_type="camera",
        provider_id=camera.provider_id,
        room=camera.room,
        capabilities=tuple(capabilities),
    )


__all__ = ["camera_to_device_manifest"]
