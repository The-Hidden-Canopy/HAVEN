"""Enrolled-devices sidecar v1 -> v2 migration and single-root data dir moves."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.web.demo import DemoDirector
from haven.web.setup_config import SetupConfigStore
from haven.web.setup_service import _ENROLLED_FILENAME, _TOKEN_FILENAME, SetupService

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _service(data_dir: Path, director: DemoDirector | None = None) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(store=store, director=director or DemoDirector(clock=lambda: NOW), clock=lambda: NOW)


def _legacy_sidecar(data_dir: Path, entries: list) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _ENROLLED_FILENAME).write_text(json.dumps({"enrolled": entries}), encoding="utf-8")


def _read_sidecar(data_dir: Path) -> dict:
    return json.loads((data_dir / _ENROLLED_FILENAME).read_text(encoding="utf-8"))


def test_legacy_v1_sidecar_migrates_to_v2_and_registers_the_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _legacy_sidecar(
            data_dir,
            [
                {
                    "candidate_id": "ble:bulb-a1f2",
                    "device_id": "ble:bulb-a1f2",
                    "device_type": "light",
                    "room": "office",
                }
            ],
        )
        director = DemoDirector(clock=lambda: NOW)
        service = SetupService(
            store=SetupConfigStore(data_dir / "haven.json"),
            director=director,
            clock=lambda: NOW,
        )

        # The migrated manifest is live in the director's registry.
        assert director.registry.is_registered("ble:bulb-a1f2")
        manifest = director.registry.get("ble:bulb-a1f2")
        assert manifest.provider_id == "demo.legacy"
        assert manifest.has_capability("power", writable=True)
        assert manifest.has_capability("brightness", writable=True)

        # The sidecar was rewritten eagerly in the v2 shape.
        payload = _read_sidecar(data_dir)
        assert payload["version"] == 2
        assert len(payload["manifests"]) == 1
        assert payload["manifests"][0]["device_id"] == "ble:bulb-a1f2"
        assert payload["manifests"][0]["provider_id"] == "demo.legacy"
        assert "enrolled" not in payload

        enrolled = service.status()["setup"]["discovery"]["enrolled"]
        assert enrolled == [
            {
                "candidate_id": "ble:bulb-a1f2",
                "device_id": "ble:bulb-a1f2",
                "device_type": "light",
                "room": "office",
            }
        ]


def test_legacy_rows_with_unknown_device_types_are_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _legacy_sidecar(
            data_dir,
            [
                {"candidate_id": "attic:sensor", "device_id": "attic:sensor", "device_type": "toaster"},
                {"candidate_id": "ble:bulb-a1f2", "device_id": "ble:bulb-a1f2", "device_type": "light"},
            ],
        )
        director = DemoDirector(clock=lambda: NOW)
        SetupService(
            store=SetupConfigStore(data_dir / "haven.json"),
            director=director,
            clock=lambda: NOW,
        )

        assert not director.registry.is_registered("attic:sensor")
        assert director.registry.is_registered("ble:bulb-a1f2")
        payload = _read_sidecar(data_dir)
        assert payload["version"] == 2
        assert [m["device_id"] for m in payload["manifests"]] == ["ble:bulb-a1f2"]


def test_v2_sidecar_re_registers_across_service_reconstruction():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        first = _service(data_dir)
        result = first.enroll("ble:bulb-a1f2", device_type="light", room="office")
        assert result["ok"] is True
        payload = _read_sidecar(data_dir)
        assert payload["version"] == 2
        assert len(payload["manifests"]) == 1

        # A restart: a fresh director over the same data dir must not forget
        # the enrolled device -- the v2 sidecar rebuilds the registry.
        restarted = DemoDirector(clock=lambda: NOW)
        assert not restarted.registry.is_registered("ble:bulb-a1f2")
        second = SetupService(
            store=SetupConfigStore(data_dir / "haven.json"),
            director=restarted,
            clock=lambda: NOW,
        )
        assert restarted.registry.is_registered("ble:bulb-a1f2")
        enrolled = second.status()["setup"]["discovery"]["enrolled"]
        assert len(enrolled) == 1
        assert enrolled[0]["device_id"] == "ble:bulb-a1f2"

        # Re-enrolling the same candidate is still refused after the restart.
        result = second.enroll("ble:bulb-a1f2", device_type="light")
        assert result["ok"] is False
        assert "already enrolled" in result["error"]


def test_choose_data_dir_moves_the_installation_to_the_new_root():
    with tempfile.TemporaryDirectory() as tmp:
        dir_a = Path(tmp) / "a"
        dir_b = Path(tmp) / "b"
        service = _service(dir_a)
        result = service.enroll("ble:bulb-a1f2", device_type="light", room="office")
        assert result["ok"] is True
        (dir_a / _TOKEN_FILENAME).write_text("secret-token", encoding="utf-8")

        result = service.choose_data_dir(str(dir_b))

        assert result["ok"] is True
        assert result["setup"]["data_dir"]["resolved"] == str(dir_b.resolve())
        # Everything moved: config, token sidecar, enrolled sidecar.
        assert (dir_b / "haven.json").is_file()
        assert (dir_b / _TOKEN_FILENAME).is_file()
        assert (dir_b / _ENROLLED_FILENAME).is_file()
        assert not (dir_a / "haven.json").exists()
        assert not (dir_a / _TOKEN_FILENAME).exists()
        assert not (dir_a / _ENROLLED_FILENAME).exists()
        # The store is rebound: a save lands in the new root.
        result = service.set_preferences(voice=True, intelligence=False)
        assert result["ok"] is True
        saved = json.loads((dir_b / "haven.json").read_text(encoding="utf-8"))
        assert saved["voice_enabled"] is True
        assert saved["data_dir"] == str(dir_b.resolve())
        assert not (dir_a / "haven.json").exists()
        # Enrollments still show after the move.
        enrolled = service.status()["setup"]["discovery"]["enrolled"]
        assert len(enrolled) == 1


def test_choose_data_dir_same_dir_is_a_no_op_move():
    with tempfile.TemporaryDirectory() as tmp:
        dir_a = Path(tmp) / "a"
        service = _service(dir_a)
        service.choose_data_dir(str(dir_a))
        assert (dir_a / "haven.json").is_file()
        assert service._store.path == dir_a / "haven.json"
