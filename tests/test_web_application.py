"""The application factory: demo fallback, real Home Assistant world, preferences."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.integrations.home_assistant import HomeAssistantWorldProvider
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.models.bridge import ModelIntelligenceProvider
from haven.web.application import HA_PROVIDER_ID, build_application
from haven.web.setup_config import SetupConfig, SetupConfigStore
from haven.web.setup_service import _ENROLLED_FILENAME, _TOKEN_FILENAME

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)

_CHANGED = "2026-09-16T19:59:30+00:00"


class _FakeHAClient:
    def fetch_states(self):
        return (
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"brightness": 128},
                "last_changed": _CHANGED,
            },
        )


def _enrolled_manifest() -> DeviceManifest:
    return DeviceManifest(
        device_id="light.living_room",
        device_type="light",
        provider_id=HA_PROVIDER_ID,
        room="living_room",
        capabilities=(
            CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_on"),
        ),
    )


def _seed_real_setup(data_dir: Path, *, voice: bool = True, intelligence: bool = True) -> SetupConfigStore:
    store = SetupConfigStore(data_dir / "haven.json")
    store.save(
        SetupConfig(
            completed=True,
            data_dir=str(data_dir),
            provider_kind="home_assistant",
            provider_base_url="http://ha.local:8123",
            provider_token_file=_TOKEN_FILENAME,
            voice_enabled=voice,
            intelligence_enabled=intelligence,
        )
    )
    (data_dir / _TOKEN_FILENAME).write_text("secret-token", encoding="utf-8")
    (data_dir / _ENROLLED_FILENAME).write_text(
        json.dumps(
            {
                "version": 2,
                "manifests": [_enrolled_manifest().to_dict()],
            }
        ),
        encoding="utf-8",
    )
    return store


def test_demo_flag_builds_the_demo_household_with_scenario_rules():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, demo=True, ha_client=_FakeHAClient()
        )

    assert director.house is not None
    assert director.garage_rule_id is not None
    assert director.camera_rule_id is not None
    assert director.office_light_rule_id is not None
    # The scenario runs: the garage-close permission request is pending.
    assert len(director.state()["pending"]) == 1


def test_configured_provider_builds_the_real_world():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp))
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    assert director.house is None
    assert isinstance(director.world, HomeAssistantWorldProvider)
    # The enrolled manifest from the v2 sidecar is registered at boot.
    assert director.registry.is_registered("light.living_room")
    manifest = director.registry.get("light.living_room")
    assert manifest.has_capability("power", writable=True)
    # The HA adapter executes for the household's provider id.
    assert director.runtime.execution_providers.get(HA_PROVIDER_ID) is not None
    # No scenario: the pre-seeded demo rules are demo-only.
    assert director.garage_rule_id is None
    assert director.camera_rule_id is None
    assert director.office_light_rule_id is None
    # The world observes through the injected client, never the network.
    snapshot = director.world.observe(NOW)
    assert [device.device_id for device in snapshot.devices] == ["light.living_room"]


def test_configured_provider_with_missing_token_falls_back_to_demo():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp))
        (Path(tmp) / _TOKEN_FILENAME).unlink()
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    assert director.house is not None
    assert not isinstance(director.world, HomeAssistantWorldProvider)


def test_intelligence_disabled_runs_the_scripted_floor():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp), intelligence=False)
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    assert isinstance(director.runtime.intelligence_provider, ScriptedIntelligenceProvider)
    assert not isinstance(director.runtime.intelligence_provider, ModelIntelligenceProvider)


def test_intelligence_enabled_keeps_the_model_bridge():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp), intelligence=True)
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    assert isinstance(director.runtime.intelligence_provider, ModelIntelligenceProvider)


def test_voice_disabled_refuses_wake():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp), voice=False)
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    result = director.voice_wake()
    assert result["ok"] is False
    texts = [entry["text"] for entry in result["state"]["conversation"]]
    assert "Voice control is disabled." in texts
