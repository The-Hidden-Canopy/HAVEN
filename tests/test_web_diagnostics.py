"""System diagnostics and backup/restore over the web surface.

Diagnostics are a network-free snapshot of one installation (world mode,
provider setup, household, devices, rules, scheduler, events, models,
voice, uptime); the provider probe is the separate user-invoked reachability
check. Backup copies every file `installation_file_names` knows about into
timestamped directories under `<data_dir>/backups/` and restores them,
stating plainly that the running process keeps its in-memory
rules/household/enrollments until a restart.
"""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.web.application import HA_PROVIDER_ID
from haven.web.demo import DemoDirector
from haven.web.diagnostics import BackupManager
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore
from haven.web.setup_service import _ENROLLED_FILENAME, _TOKEN_FILENAME, SetupService

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
_CHANGED = "2026-09-16T19:59:30+00:00"


class _StubStatesSource:
    """The structural `fetch_states()` contract, canned for tests."""

    def __init__(self, states) -> None:
        self._states = states

    def fetch_states(self) -> tuple[dict, ...]:
        return self._states


class _RaisingStatesSource:
    def fetch_states(self) -> tuple[dict, ...]:
        raise ConnectionError("provider is down")


LIVE_STATES = (
    {"entity_id": "light.office_desk", "state": "on", "attributes": {}, "last_changed": _CHANGED},
    {
        "entity_id": "binary_sensor.gerron_office_occupancy",
        "state": "on",
        "attributes": {},
        "last_changed": _CHANGED,
    },
)


def _seed_real_setup(data_dir: Path) -> None:
    """Persist a configured-provider installation, like the factory reads."""
    data_dir.mkdir(parents=True, exist_ok=True)
    store = SetupConfigStore(data_dir / "haven.json")
    store.save(
        SetupConfig(
            completed=True,
            data_dir=str(data_dir),
            provider_kind="home_assistant",
            provider_base_url="http://ha.local:8123",
            provider_token_file=_TOKEN_FILENAME,
        )
    )
    (data_dir / _TOKEN_FILENAME).write_text("secret-token", encoding="utf-8")
    manifest = DeviceManifest(
        device_id="light.office_desk",
        device_type="light",
        provider_id=HA_PROVIDER_ID,
        room="office",
        capabilities=(
            CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_on"),
        ),
    )
    (data_dir / _ENROLLED_FILENAME).write_text(
        json.dumps({"version": 2, "manifests": [manifest.to_dict()]}),
        encoding="utf-8",
    )


@contextmanager
def _boot(data_dir: Path, ha_client=None):
    instance, director = make_server(
        0,
        data_dir=str(data_dir),
        clock=lambda: NOW,
        ha_client=ha_client,
        models_root=data_dir / "models-root",
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, director, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else ""
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def _service(data_dir: Path) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(store=store, director=DemoDirector(clock=lambda: NOW), clock=lambda: NOW)


def test_diagnostics_shape_in_real_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            # A declared owner first: enrollment now refuses without one.
            status, body = _post(
                port,
                "/api/setup/household/people",
                {
                    "name": "Gerron Smith",
                    "entity_id": "binary_sensor.gerron_office_occupancy",
                    "room_id": "office",
                    "role": "owner",
                },
            )
            assert status == 200
            # One more enrolled device, via the API.
            status, body = _post(port, "/api/setup/enroll", {"candidate_id": "ble:bulb-a1f2", "device_type": "light"})
            assert status == 200

            status, body = _get_json(port, "/api/system/diagnostics")
            assert status == 200
            assert body["ok"] is True
            diag = body["diagnostics"]
            assert set(diag) == {
                "data_dir",
                "config_error",
                "world",
                "provider",
                "household",
                "devices",
                "rules",
                "scheduler",
                    "events",
                    "knowledge",
                    "models",
                "voice",
                "uptime_seconds",
            }
            assert diag["data_dir"] == str(data_dir)
            assert diag["config_error"] is None
            assert diag["world"] == {"mode": "home_assistant"}
            assert diag["provider"] == {
                "configured": True,
                "kind": "home_assistant",
                "base_url": "http://ha.local:8123",
            }
            assert diag["household"] == {"people": 1, "contexts": 0}
            assert diag["devices"] == {"enrolled": 2, "registered": 2}
            assert diag["rules"]["total"] == diag["rules"]["approved"] + diag["rules"]["proposed"]
            assert diag["scheduler"] == {"entries": 0, "enabled": 0}
            assert diag["events"] >= 0
            assert diag["models"] == {"registered": 0, "loaded": 0, "assigned_roles": 0}
            assert diag["voice"]["enabled"] is False
            assert diag["voice"]["state"] == "dormant"
            assert diag["uptime_seconds"] >= 0


def test_diagnostics_in_demo_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        with _boot(data_dir) as (_, director, port):
            assert director.house is not None
            status, body = _get_json(port, "/api/system/diagnostics")
            assert status == 200
            diag = body["diagnostics"]
            assert diag["world"] == {"mode": "demo"}
            assert diag["provider"] == {"configured": False, "kind": None, "base_url": None}
            # The demo household is the fallback world: five demo devices,
            # three approved scenario rules, one enabled schedule.
            assert diag["devices"]["registered"] == 5
            assert diag["devices"]["enrolled"] == 0
            assert diag["rules"]["approved"] == 3
            assert diag["scheduler"]["entries"] == 3
            assert diag["scheduler"]["enabled"] == 1


def test_probe_provider_reachable_unreachable_and_demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _post(port, "/api/system/diagnostics/probe")
            assert status == 200
            assert body == {"ok": True, "reachable": True, "detail": f"{len(LIVE_STATES)} states fetched"}

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        with _boot(data_dir, _RaisingStatesSource()) as (_, _, port):
            status, body = _post(port, "/api/system/diagnostics/probe")
            assert status == 200
            assert body["ok"] is True
            assert body["reachable"] is False
            assert "ConnectionError" in body["detail"]

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        with _boot(data_dir) as (_, _, port):
            status, body = _post(port, "/api/system/diagnostics/probe")
            assert status == 400
            assert body == {"ok": False, "error": "no provider attached"}


def test_backup_lifecycle_create_list_restore_delete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            # Era one: voice on, one declared person.
            status, body = _post(port, "/api/setup/preferences", {"voice": True, "intelligence": False})
            assert status == 200
            status, body = _post(
                port,
                "/api/setup/household/people",
                {"name": "Gerron Smith", "entity_id": "binary_sensor.gerron_office_occupancy", "room_id": "office"},
            )
            assert status == 200
            status, body = _post(port, "/api/system/backup")
            assert status == 200
            first_id = body["backup"]["id"]
            # Every file that actually exists at backup time, not a
            # hardcoded five: the real director's history.db and the
            # server's resources.db/ontology.db/action_ledger.db exist from
            # boot onward too.
            assert set(body["backup"]["files"]) == {
                "haven.json",
                "household.json",
                "enrolled_devices.json",
                "ha_token.txt",
                "history.db",
                "resources.db",
                    "ontology.db",
                    "claims.db",
                    "action_ledger.db",
            }

            # Era two: voice off.
            status, body = _post(port, "/api/setup/preferences", {"voice": False, "intelligence": False})
            assert status == 200
            status, body = _post(port, "/api/system/backup")
            assert status == 200
            second_id = body["backup"]["id"]

            status, body = _get_json(port, "/api/system/backups")
            assert status == 200
            assert [entry["id"] for entry in body["backups"]] == sorted(
                [first_id, second_id], reverse=True
            )

            # Path traversal and unknown ids refuse with a 400.
            for bad in ("../x", "a/b", "..", "ghost"):
                status, body = _post(port, "/api/system/backup/restore", {"id": bad})
                assert status == 400
                assert body["ok"] is False
                status, body = _post(port, "/api/system/backup/delete", {"id": bad})
                assert status == 400
                assert body["ok"] is False

            # Restore era one: the file on disk says voice on again, and the
            # running process is told the truth about its boundary.
            status, body = _post(port, "/api/system/backup/restore", {"id": first_id})
            assert status == 200
            assert body["result"]["restart_required"] is True
            assert "haven.json" in body["result"]["restored"]
            saved = json.loads((data_dir / "haven.json").read_text(encoding="utf-8"))
            assert saved["voice_enabled"] is True

            # The restore is real: a fresh boot on the same data dir reads
            # era one's configuration.
            status, body = _post(port, "/api/system/backup/delete", {"id": first_id})
            assert status == 200
            status, body = _get_json(port, "/api/system/backups")
            assert [entry["id"] for entry in body["backups"]] == [second_id]

        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _get_json(port, "/api/setup")
            assert status == 200
            assert body["setup"]["preferences"]["voice"] is True


def test_choose_data_dir_moves_backups_with_the_installation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dir_a = Path(tmp) / "a"
        dir_b = Path(tmp) / "b"
        dir_a.mkdir(parents=True)
        manager = BackupManager(data_dir=dir_a)
        created = manager.create()
        assert (dir_a / "backups" / created["id"]).is_dir()

        service = _service(dir_a)
        result = service.choose_data_dir(str(dir_b))
        assert result["ok"] is True
        assert (dir_b / "backups" / created["id"]).is_dir()
        assert not (dir_a / "backups").exists()
        moved = BackupManager(data_dir=dir_b)
        assert [entry["id"] for entry in moved.list()["backups"]] == [created["id"]]


def test_choose_data_dir_moves_and_rebinds_resources_and_ontology() -> None:
    """Regression: `resources.db`/`ontology.db`/`computer_provider.json`
    used to be left behind entirely -- `choose_data_dir` never listed them,
    and even once moved, `HavenWebServer.resources`/`.ontology` (constructed
    once at boot) would keep pointing at the old, now-empty location."""

    with tempfile.TemporaryDirectory() as tmp:
        old_dir = str(Path(tmp) / "old")
        new_dir = Path(tmp) / "new"
        with _boot(Path(old_dir)) as (server, _director, port):
            from haven.resources import ResourceRecord

            server.resources.save(
                ResourceRecord(
                    resource_id="file:before-move.txt",
                    resource_type="file",
                    scope_id="project:haven",
                    provider_id="local_computer",
                    title="before-move.txt",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )

            status, body = _post(port, "/api/setup/data-dir", {"path": str(new_dir)})
            assert status == 200
            assert body["ok"] is True

            # The files themselves moved...
            assert (new_dir / "resources.db").exists()
            assert (new_dir / "ontology.db").exists()
            assert not (Path(old_dir) / "resources.db").exists()

            # ...and the live server's own store objects were rebound to
            # the new location, not left pointing at the old, moved-away
            # file: both the data saved before the move is still readable,
            # and newly saved data lands in the new location.
            assert server.resources.path == new_dir / "resources.db"
            assert server.resources.get("file:before-move.txt") is not None
            status, body = _get_json(port, "/api/search?q=before-move")
            assert status == 200
            assert body["hits"] and body["hits"][0]["resource_id"] == "file:before-move.txt"
