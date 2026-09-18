"""Principals derived from household declarations.

HAVEN acts as the household's declared people: a declared person's id is the
actor on governed flows, and the declared owner (or the first declared person,
in a one-person household) supplies the approving tier. The demo household
declares gerron by construction, so a bare director keeps the bootstrap
identity.
"""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from haven.core.domain import Principal, RoleTier
from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.web.application import HA_PROVIDER_ID
from haven.web.demo import HOUSEHOLD_ID, DemoDirector
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore
from haven.web.setup_service import (
    _ENROLLED_FILENAME,
    _HOUSEHOLD_FILENAME,
    _TOKEN_FILENAME,
    SetupService,
    load_household_declarations,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
_CHANGED = "2026-09-16T19:59:30+00:00"


class _StubStatesSource:
    """The structural `fetch_states()` contract, canned for tests."""

    def __init__(self, states) -> None:
        self._states = states

    def fetch_states(self) -> tuple[dict, ...]:
        return self._states


LIVE_STATES = (
    {"entity_id": "light.office_desk", "state": "on", "attributes": {}, "last_changed": _CHANGED},
)


def _service(data_dir: Path, director: DemoDirector | None = None) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(store=store, director=director or DemoDirector(clock=lambda: NOW), clock=lambda: NOW)


def _seed_real_setup(data_dir: Path, household: dict) -> None:
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
            CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
        ),
    )
    (data_dir / _ENROLLED_FILENAME).write_text(
        json.dumps({"version": 2, "manifests": [manifest.to_dict()]}),
        encoding="utf-8",
    )
    (data_dir / _HOUSEHOLD_FILENAME).write_text(json.dumps(household), encoding="utf-8")


@contextmanager
def _boot(data_dir: Path, ha_client):
    instance, director = make_server(0, data_dir=str(data_dir), clock=lambda: NOW, ha_client=ha_client)
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


# -- declaration roles ----------------------------------------------------------


def test_declare_person_accepts_owner_and_member_roles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        member = service.declare_person(
            name="Ada Lovelace", entity_id="binary_sensor.ada_office", room_id="office"
        )
        assert member["ok"] is True
        assert member["setup"]["household"]["people"][0]["role"] == "member"

        owner = service.declare_person(
            name="Charles Babbage", entity_id="binary_sensor.charles_study", room_id="study", role="owner"
        )
        assert owner["ok"] is True
        people = owner["setup"]["household"]["people"]
        assert next(p for p in people if p["person_id"] == "charles_babbage")["role"] == "owner"


def test_declare_person_rejects_an_invalid_role() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.declare_person(
            name="Ada Lovelace", entity_id="binary_sensor.ada_office", room_id="office", role="admin"
        )
        assert result["ok"] is False
        assert "role" in result["error"]
        assert service.status()["setup"]["household"]["people"] == []


def test_declare_person_updates_the_role_of_an_existing_person() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        declared = service.declare_person(
            name="Ada Lovelace", entity_id="binary_sensor.ada_office", room_id="office"
        )
        assert declared["setup"]["household"]["people"][0]["role"] == "member"

        promoted = service.declare_person(
            name="Ada Lovelace", entity_id="binary_sensor.ada_office", room_id="office", role="owner"
        )
        assert promoted["ok"] is True
        people = promoted["setup"]["household"]["people"]
        assert len(people) == 1
        assert people[0]["role"] == "owner"
        # The role update is a declaration change, not a new source.
        assert people[0]["sources"] == [{"entity_id": "binary_sensor.ada_office", "room_id": "office"}]

        # The update is persisted: a fresh service sees the owner role.
        second = _service(Path(tmp) / "data")
        assert second.status()["setup"]["household"]["people"][0]["role"] == "owner"


def test_old_sidecar_without_role_keys_loads_as_member() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "household.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "people": [
                        {
                            "person_id": "gerron",
                            "name": "Gerron",
                            "sources": [{"entity_id": "binary_sensor.gerron_office", "room_id": "office"}],
                        }
                    ],
                    "contexts": [],
                }
            ),
            encoding="utf-8",
        )
        declarations = load_household_declarations(path)
        (person,) = declarations.people
        assert person.role == "member"


# -- principals on governed flows -------------------------------------------------


def _direct_light_off(director: DemoDirector) -> None:
    result = director.device_command("office_light", "light.turn_off")
    assert result["ok"] is True


def test_injected_principals_are_the_actor_on_a_direct_command() -> None:
    director = DemoDirector(
        clock=lambda: NOW,
        resident=Principal(actor_id="ada", household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER),
        owner=Principal(actor_id="ada", household_id=HOUSEHOLD_ID, role_tier=RoleTier.OWNER),
    )
    _direct_light_off(director)
    action = next(a for a in director.store.state.actions if a.status.value == "executed")
    assert action.request.requested_by == "ada"
    assert action.request.requested_by != "gerron"


def test_default_director_keeps_the_demo_identity() -> None:
    director = DemoDirector(clock=lambda: NOW)
    assert director.resident.actor_id == "gerron"
    assert director.owner.actor_id == "gerron"
    _direct_light_off(director)
    action = next(a for a in director.store.state.actions if a.status.value == "executed")
    assert action.request.requested_by == "gerron"


# -- factory acceptance: HAVEN acts as the declared person ------------------------


def test_factory_derives_the_actor_from_the_declared_owner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(
            data_dir,
            household={
                "version": 1,
                "people": [
                    {
                        "person_id": "ada_lovelace",
                        "name": "Ada Lovelace",
                        "role": "owner",
                        "sources": [{"entity_id": "binary_sensor.ada_office", "room_id": "office"}],
                    }
                ],
                "contexts": [],
            },
        )
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _post(port, "/api/devices/light.office_desk/command", {"service": "light.turn_off"})
            assert status == 200
            assert body["ok"] is True

            _, state = _get_json(port, "/api/state")
            event_id = next(row["event_id"] for row in state["activity"] if row["event_type"] == "action_executed")
            status, body = _get_json(port, f"/api/actions/chain?event_id={event_id}")
            assert status == 200
            assert body["ok"] is True
            # HAVEN acted as the declared person, not the demo bootstrap identity.
            assert body["chain"]["request"]["actor"] == "ada_lovelace"
            assert body["chain"]["request"]["actor"] != "gerron"


def test_factory_falls_back_to_the_bootstrap_identity_without_declarations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir, household={"version": 1, "people": [], "contexts": []})
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _post(port, "/api/devices/light.office_desk/command", {"service": "light.turn_off"})
            assert status == 200

            _, state = _get_json(port, "/api/state")
            event_id = next(row["event_id"] for row in state["activity"] if row["event_type"] == "action_executed")
            _, body = _get_json(port, f"/api/actions/chain?event_id={event_id}")
            assert body["chain"]["request"]["actor"] == "gerron"
