"""Camera discovery: CameraManifest + CameraRegistry.

This proves only the "discover / add cameras" piece: registering what a
provider found, and looking it up by camera_id, room, or required hardware
capability. Nothing here connects to a stream, moves a PTZ motor, or
records anything.
"""

import pytest

from haven.cameras import CameraCapabilities, CameraManifest, CameraRegistry, UnknownCamera


def _driveway_camera() -> CameraManifest:
    return CameraManifest(
        camera_id="driveway_cam",
        provider_id="onvif",
        room="driveway",
        capabilities=CameraCapabilities(
            live_stream=True,
            recording=True,
            ptz=True,
            optical_zoom=True,
            microphone=False,
            speaker=False,
            infrared_mode=True,
            privacy_shutter=False,
        ),
    )


def _bedroom_camera() -> CameraManifest:
    return CameraManifest(
        camera_id="bedroom_cam",
        provider_id="home_assistant",
        room="bedroom",
        capabilities=CameraCapabilities(live_stream=True, recording=True, privacy_shutter=True),
    )


def test_capabilities_default_to_false_and_expose_lookup_by_name():
    caps = CameraCapabilities(live_stream=True, ptz=True)

    assert caps.has("live_stream") is True
    assert caps.has("recording") is False
    assert caps.has("privacy_shutter") is False
    with pytest.raises(ValueError):
        caps.has("night_vision")  # not a declared capability field


def test_registry_lookup_by_camera_id():
    registry = CameraRegistry()
    camera = _driveway_camera()
    registry.register(camera)

    assert registry.is_registered("driveway_cam")
    assert registry.get("driveway_cam") is camera


def test_registry_unknown_camera_raises():
    registry = CameraRegistry()

    with pytest.raises(UnknownCamera):
        registry.get("nope")
    assert registry.is_registered("nope") is False


def test_find_filters_by_room_and_required_capabilities():
    registry = CameraRegistry()
    registry.register(_driveway_camera())
    registry.register(_bedroom_camera())

    assert registry.find(requires=("ptz",)) == ("driveway_cam",)
    assert registry.find(room="bedroom") == ("bedroom_cam",)
    assert set(registry.find(requires=("live_stream", "recording"))) == {"driveway_cam", "bedroom_cam"}
    assert registry.find(room="driveway", requires=("privacy_shutter",)) == ()


def test_all_cameras_lists_every_registered_manifest():
    registry = CameraRegistry()
    registry.register(_driveway_camera())
    registry.register(_bedroom_camera())

    assert {c.camera_id for c in registry.all_cameras()} == {"driveway_cam", "bedroom_cam"}


def test_manifest_requires_a_capabilities_value():
    with pytest.raises(ValueError):
        CameraManifest(camera_id="x", provider_id="onvif", capabilities="not-a-capabilities-object")
