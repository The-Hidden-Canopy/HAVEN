"""Home Assistant entities as setup discovery enrollment candidates."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.devices import ControlClass
from haven.web.demo import DemoDirector
from haven.web.setup_config import SetupConfigStore
from haven.web.setup_service import _ENROLLED_FILENAME, SetupService

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)

# The kitchen light exercises single-token room derivation (no room guess);
# the sensor is an unsupported domain and the dead bulb is unavailable, so
# both must be skipped.
CANNED_STATES = (
    {"entity_id": "light.office_desk", "state": "on"},
    {"entity_id": "switch.bedroom_fan", "state": "off"},
    {"entity_id": "climate.living_room", "state": "heat"},
    {"entity_id": "sensor.temperature_1", "state": "21.5"},
    {"entity_id": "light.dead_bulb", "state": "unavailable"},
    {"entity_id": "cover.garage_door", "state": "open"},
    {"entity_id": "light.kitchen", "state": "on"},
)


class _StubStatesSource:
    """The structural `fetch_states()` contract, canned for tests."""

    def __init__(self, states=CANNED_STATES, exc: Exception | None = None) -> None:
        self._states = states
        self._exc = exc

    def fetch_states(self) -> tuple[dict, ...]:
        if self._exc is not None:
            raise self._exc
        return self._states


def _service(data_dir: Path, director: DemoDirector | None = None, source=None) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(
        store=store,
        director=director or DemoDirector(clock=lambda: NOW),
        clock=lambda: NOW,
        ha_states_source=source,
        # This file is specifically about the demo+HA merge/degrade
        # behavior, so it opts into the demo fixture set explicitly --
        # SetupService defaults it off so a real household's first scan
        # never shows these as if they were real nearby devices.
        include_demo_candidates=True,
    )


def _candidates_by_id(body: dict) -> dict[str, dict]:
    return {candidate["candidate_id"]: candidate for candidate in body["candidates"]}


def test_scan_without_a_source_returns_the_demo_candidates_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.run_discovery()
        assert result["ok"] is True
        assert sorted(candidate["candidate_id"] for candidate in result["candidates"]) == sorted(
            ["ble:bulb-a1f2", "mdns:therm-living", "wifi:plug-heater"]
        ), "candidate ids"
        assert all("home_assistant" not in candidate["candidate_id"] for candidate in result["candidates"])


def test_scan_with_a_source_merges_real_ha_entities() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data", source=_StubStatesSource())
        result = service.run_discovery()
        assert result["ok"] is True
        candidates = _candidates_by_id(result)

        # Demo 3 + 5 HA candidates (sensor skipped, unavailable bulb skipped).
        assert len(candidates) == 8
        desk = candidates["light.office_desk"]
        assert desk["provider_id"] == "home_assistant"
        assert desk["source"] == "home_assistant.states"
        assert desk["suggested_device_type"] == "light"
        assert desk["suggested_room"] == "office"
        assert desk["signal_strength"] is None
        assert desk["enrolled"] is False
        assert candidates["switch.bedroom_fan"]["suggested_device_type"] == "switch"
        assert candidates["climate.living_room"]["suggested_device_type"] == "thermostat"
        assert candidates["cover.garage_door"]["suggested_device_type"] == "cover"
        # A single token after the domain carries no room guess.
        assert candidates["light.kitchen"]["suggested_room"] is None
        # Unsupported domains and dead entities are not candidates.
        assert "sensor.temperature_1" not in candidates
        assert "light.dead_bulb" not in candidates


def test_scan_degrades_to_demo_candidates_when_the_source_is_unreachable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data", source=_StubStatesSource(exc=ConnectionError("refused")))
        result = service.run_discovery()
        assert result["ok"] is True
        assert sorted(candidate["candidate_id"] for candidate in result["candidates"]) == sorted(
            ["ble:bulb-a1f2", "mdns:therm-living", "wifi:plug-heater"]
        )


def test_enroll_ha_candidate_merges_enrolled_flag_and_routes_to_the_provider() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        director = DemoDirector(clock=lambda: NOW)
        service = _service(data_dir, director=director)
        service.attach_ha_states_source(_StubStatesSource())

        result = service.enroll("light.office_desk", device_type="light", room="office")
        assert result["ok"] is True
        manifest = director.registry.get("light.office_desk")
        # The manifest's provider_id routes execution to the live adapter.
        assert manifest.provider_id == "home_assistant"
        assert manifest.device_type == "light"
        assert manifest.room == "office"

        # A fresh scan merges the enrollment state into the HA candidate too.
        candidates = _candidates_by_id(service.run_discovery())
        assert candidates["light.office_desk"]["enrolled"] is True
        assert candidates["light.kitchen"]["enrolled"] is False

        enrolled = result["setup"]["discovery"]["enrolled"]
        assert enrolled == [
            {
                "candidate_id": "light.office_desk",
                "device_id": "light.office_desk",
                "device_type": "light",
                "room": "office",
            }
        ]

        # Persistence: a fresh director over the same config dir re-registers
        # the HA-enrolled manifest from the v2 sidecar.
        restarted = DemoDirector(clock=lambda: NOW)
        assert not restarted.registry.is_registered("light.office_desk")
        second = _service(data_dir, director=restarted)
        assert restarted.registry.is_registered("light.office_desk")
        assert restarted.registry.get("light.office_desk").provider_id == "home_assistant"
        assert second._enrolled_path().name == _ENROLLED_FILENAME
        enrolled = second.status()["setup"]["discovery"]["enrolled"]
        assert enrolled[0]["device_id"] == "light.office_desk"
        assert len(enrolled) == 1


def test_fan_and_cover_presets_enroll_guarded_capabilities() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        director = DemoDirector(clock=lambda: NOW)
        service = _service(Path(tmp) / "data", director=director, source=_StubStatesSource())

        result = service.enroll("switch.bedroom_fan", device_type="fan", room="bedroom")
        assert result["ok"] is True
        fan = director.registry.get("switch.bedroom_fan")
        assert fan.provider_id == "home_assistant"
        assert fan.has_capability("power", writable=True)
        power = next(cap for cap in fan.capabilities if cap.name == "power")
        assert power.service == "fan.turn_off"

        result = service.enroll("cover.garage_door", device_type="cover")
        assert result["ok"] is True
        cover = director.registry.get("cover.garage_door")
        close = next(cap for cap in cover.capabilities if cap.name == "close")
        open_cap = next(cap for cap in cover.capabilities if cap.name == "open")
        assert close.service == "cover.close"
        assert open_cap.service == "cover.open"
        assert close.control_class is ControlClass.GUARDED
        assert open_cap.control_class is ControlClass.GUARDED
        # The default room falls back to the candidate's suggestion.
        assert cover.room == "garage"


def test_camera_candidate_enrolls_as_observation_only() -> None:
    """A camera discovered through HA state enrolls, but never claims control.

    Before this preset existed, `_CAPABILITY_PRESETS` had no "camera" entry
    at all, so discovery could surface a camera candidate that enrollment
    then flatly refused with "unsupported device_type" -- exactly the
    discovery/enrollment mismatch this covers. The fix must not overclaim
    control HA's camera domain can't back: the manifest carries a single
    readable capability, and nothing writable.
    """

    with tempfile.TemporaryDirectory() as tmp:
        director = DemoDirector(clock=lambda: NOW)
        camera_states = CANNED_STATES + (
            {"entity_id": "camera.driveway", "state": "idle"},
        )
        service = _service(Path(tmp) / "data", director=director, source=_StubStatesSource(camera_states))

        candidates = _candidates_by_id(service.run_discovery())
        assert candidates["camera.driveway"]["suggested_device_type"] == "camera"

        result = service.enroll("camera.driveway", device_type="camera", room="driveway")
        assert result["ok"] is True
        manifest = director.registry.get("camera.driveway")
        assert manifest.provider_id == "home_assistant"
        assert [cap.name for cap in manifest.capabilities] == ["live_stream"]
        assert manifest.capabilities[0].readable is True
        assert manifest.capabilities[0].writable is False
        assert not any(cap.writable for cap in manifest.capabilities)

        # Honest refusal, not a fabricated capability: nothing on this
        # manifest can actually be commanded.
        denied = director.device_command("camera.driveway", "camera.turn_on")
        assert denied["ok"] is False
