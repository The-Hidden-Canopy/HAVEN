"""The application factory: demo fallback, real Home Assistant world, preferences."""

import json
import tempfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from haven.core.domain import DeviceResult
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.integrations.home_assistant import HomeAssistantWorldProvider
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.models.bridge import ModelIntelligenceProvider
from haven.providers.plugin import ProviderManifest
from haven.web.application import HA_PROVIDER_ID, build_application, ensure_household_id
from haven.web.demo import HOUSEHOLD_ID
from haven.web.provider_install import save_installed_provider
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
    # A real household never carries the shared demo fixture id.
    assert director.household_id != HOUSEHOLD_ID
    assert director.resident.household_id == director.household_id
    assert director.owner.household_id == director.household_id


def test_household_id_is_minted_once_and_persisted_across_boots():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp))
        first = build_application(store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient())
        # The mint was persisted: a fresh SetupConfigStore reading the same
        # file sees the id build_application just wrote, not None.
        reloaded_id = SetupConfigStore(store.path).load().household_id
        assert reloaded_id == first.household_id

        second = build_application(store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient())
        assert second.household_id == first.household_id


def test_two_installations_never_share_a_household_id():
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        store_a = _seed_real_setup(Path(tmp_a))
        store_b = _seed_real_setup(Path(tmp_b))
        director_a = build_application(store=store_a, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient())
        director_b = build_application(store=store_b, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient())
        assert director_a.household_id != director_b.household_id


def test_ensure_household_id_mints_only_when_absent():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        config = SetupConfig(provider_kind="home_assistant")
        updated, minted = ensure_household_id(store, config)
        assert minted
        assert updated.household_id == minted

        again, unchanged = ensure_household_id(store, updated)
        assert unchanged == minted


def test_configured_provider_with_missing_token_stays_real_but_unreachable():
    with tempfile.TemporaryDirectory() as tmp:
        store = _seed_real_setup(Path(tmp))
        (Path(tmp) / _TOKEN_FILENAME).unlink()
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    # An unreadable token must never produce the demo fixture: this is the
    # household's real installation (its enrolled device is still there),
    # just with a world reporting no live evidence rather than a fictional
    # house that looks real.
    assert director.house is None
    assert isinstance(director.world, HomeAssistantWorldProvider)
    assert director.registry.is_registered("light.living_room")
    assert director.ha_states_source is None
    snapshot = director.world.observe(NOW)
    assert snapshot.devices == ()
    # No execution provider either: nothing can reach a command anywhere.
    assert director.runtime.execution_providers.is_registered(HA_PROVIDER_ID) is False


def test_configured_provider_with_no_base_url_stays_real_but_unreachable():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        store.save(SetupConfig(completed=True, data_dir=str(data_dir), provider_kind="home_assistant"))
        director = build_application(
            store=store, model_manager=None, clock=lambda: NOW, ha_client=_FakeHAClient()
        )

    assert director.house is None
    assert isinstance(director.world, HomeAssistantWorldProvider)
    assert director.ha_states_source is None


class _FakePhilipsHueProvider:
    """Both an `ObservationProvider` and an `ExecutionAdapter`, like `BluetoothProvider`."""

    def __init__(self, config):
        self.config = dict(config)

    def observe(self):
        from haven.core.domain import DeviceState

        return (
            DeviceState(
                device_id="hue.living_room",
                kind="light",
                room_id="living_room",
                is_on=True,
                brightness_pct=80,
                observed_at=NOW,
                source="philips_hue",
            ),
        )

    def execute(self, command):
        return DeviceResult(success=True, detail="ok", observed_at=NOW, source="philips_hue")


class FakeHuePluginForApplicationTest:
    def describe(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="philips_hue",
            kind="execution",
            capabilities=frozenset({"light.turn_on"}),
            display_name="Philips Hue",
            description="test fixture",
        )

    def build(self, *, config):
        return _FakePhilipsHueProvider(config)


FAKE_HUE_PLUGIN = FakeHuePluginForApplicationTest()


def test_activated_community_provider_supplies_real_world_and_execution(monkeypatch):
    fake_entry_point = metadata.EntryPoint(
        name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers"
    )
    monkeypatch.setattr(
        metadata, "entry_points", lambda *, group: (fake_entry_point,) if group == "haven.providers" else ()
    )

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        store.save(SetupConfig(completed=True, data_dir=str(data_dir), provider_kind="philips_hue"))
        save_installed_provider(
            store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"}
        )
        director = build_application(store=store, model_manager=None, clock=lambda: NOW)

    assert director.house is None
    snapshot = director.world.observe(NOW)
    assert [d.device_id for d in snapshot.devices] == ["hue.living_room"]
    assert director.runtime.execution_providers.get("philips_hue") is not None


def test_a_disabled_installed_provider_is_treated_as_unavailable(monkeypatch):
    fake_entry_point = metadata.EntryPoint(
        name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers"
    )
    monkeypatch.setattr(
        metadata, "entry_points", lambda *, group: (fake_entry_point,) if group == "haven.providers" else ()
    )

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        store.save(SetupConfig(completed=True, data_dir=str(data_dir), provider_kind="philips_hue"))
        save_installed_provider(
            store, provider_id="philips_hue", entry_point_name="philips_hue", config={"bridge_ip": "10.0.0.5"}
        )
        from haven.web.provider_install import set_installed_provider_enabled

        set_installed_provider_enabled(store, "philips_hue", False)
        director = build_application(store=store, model_manager=None, clock=lambda: NOW)

    snapshot = director.world.observe(NOW)
    assert snapshot.devices == ()
    assert director.runtime.execution_providers.is_registered("philips_hue") is False


def test_unknown_provider_kind_stays_real_but_unreachable():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        store = SetupConfigStore(data_dir / "haven.json")
        # A provider this composition root has no adapter for at all -- a
        # community provider (Philips Hue, Google Home, Matter, ...) whose
        # own package would register an adapter this repo never imports.
        # This must never quietly become the demo fixture: a household that
        # has named ANY provider gets its own real, if currently
        # evidence-less, installation.
        store.save(SetupConfig(completed=True, data_dir=str(data_dir), provider_kind="philips_hue"))
        (data_dir / _ENROLLED_FILENAME).write_text(
            json.dumps({"version": 2, "manifests": [_enrolled_manifest().to_dict()]}), encoding="utf-8"
        )
        director = build_application(store=store, model_manager=None, clock=lambda: NOW)

    assert director.house is None
    assert isinstance(director.world, HomeAssistantWorldProvider)
    assert director.registry.is_registered("light.living_room")
    assert director.ha_states_source is None
    snapshot = director.world.observe(NOW)
    assert snapshot.devices == ()
    assert director.runtime.execution_providers.is_registered(HA_PROVIDER_ID) is False
    # A real household id was minted, not the shared demo fixture id.
    assert director.household_id != HOUSEHOLD_ID


def test_no_provider_configured_yet_builds_the_demo_household():
    with tempfile.TemporaryDirectory() as tmp:
        store = SetupConfigStore(Path(tmp) / "haven.json")
        director = build_application(store=store, model_manager=None, clock=lambda: NOW)

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
