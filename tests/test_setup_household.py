"""Household declarations: people, presence sources, and context meanings.

The setup layer persists who lives here (`household.json` next to the other
setup sidecars), and the real-mode composition root wires the declarations
into the Home Assistant observer, so a declared person reaches the UI state.
"""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.web.application import HA_PROVIDER_ID
from haven.web.demo import DemoDirector
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigError, SetupConfigStore
from haven.web.setup_service import (
    _ENROLLED_FILENAME,
    _HOUSEHOLD_FILENAME,
    _TOKEN_FILENAME,
    HouseholdDeclarations,
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


# The office light is an enrolled device (so the office room exists in state);
# the occupancy sensor and the input boolean carry the declared meaning.
LIVE_STATES = (
    {"entity_id": "light.office_desk", "state": "on", "attributes": {}, "last_changed": _CHANGED},
    {
        "entity_id": "binary_sensor.gerron_office_occupancy",
        "state": "on",
        "attributes": {},
        "last_changed": _CHANGED,
    },
    {"entity_id": "input_boolean.working_late", "state": "on", "attributes": {}, "last_changed": _CHANGED},
)


def _service(data_dir: Path, director: DemoDirector | None = None) -> SetupService:
    store = SetupConfigStore(data_dir / "haven.json")
    return SetupService(store=store, director=director or DemoDirector(clock=lambda: NOW), clock=lambda: NOW)


def _seed_real_setup(data_dir: Path, household: dict | None = None) -> None:
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
    if household is not None:
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


def test_declare_person_creates_with_derived_id_and_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        service = _service(data_dir)
        result = service.declare_person(
            name="Gerron Smith", entity_id="binary_sensor.gerron_office_occupancy", room_id="office"
        )
        assert result["ok"] is True
        person = result["setup"]["household"]["people"][0]
        assert person == {
            "person_id": "gerron_smith",
            "name": "Gerron Smith",
            "role": "member",
            "sources": [{"entity_id": "binary_sensor.gerron_office_occupancy", "room_id": "office"}],
        }

        # A second SetupService over the same dir sees the declaration.
        second = _service(data_dir)
        assert second.status()["setup"]["household"]["people"][0]["person_id"] == "gerron_smith"


def test_declare_person_is_idempotent_and_conflicts_on_a_different_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.declare_person(name="Gerron Smith", entity_id="binary_sensor.gerron_office", room_id="office")
        assert result["ok"] is True

        again = service.declare_person(name="Gerron Smith", entity_id="binary_sensor.gerron_office", room_id="office")
        assert again["ok"] is True
        assert len(again["setup"]["household"]["people"]) == 1
        assert len(again["setup"]["household"]["people"][0]["sources"]) == 1

        # The same derived id under a different spelling is a conflict.
        conflict = service.declare_person(name="gerron-smith", entity_id="binary_sensor.gerron_bed", room_id="bedroom")
        assert conflict["ok"] is False
        assert "different name" in conflict["error"]

        # A new entity for the same person accumulates as another source.
        extra = service.declare_person(name="Gerron Smith", entity_id="binary_sensor.gerron_bed", room_id="bedroom")
        assert extra["ok"] is True
        sources = extra["setup"]["household"]["people"][0]["sources"]
        assert sources == [
            {"entity_id": "binary_sensor.gerron_office", "room_id": "office"},
            {"entity_id": "binary_sensor.gerron_bed", "room_id": "bedroom"},
        ]


def test_declare_person_rejects_blank_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.declare_person(name="  ", entity_id="binary_sensor.gerron_office", room_id="office")
        assert result["ok"] is False
        result = service.declare_person(name="Gerron", entity_id="binary_sensor.gerron_office", room_id="")
        assert result["ok"] is False


def test_declare_person_with_no_presence_sensor_yet() -> None:
    """A person (including the owner) can be declared with just a name and
    role -- HAVEN needs to know who owns the installation before it needs
    to know how presence is sensed."""

    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.declare_person(name="Gerron Smith", role="owner")
        assert result["ok"] is True
        person = result["setup"]["household"]["people"][0]
        assert person == {"person_id": "gerron_smith", "name": "Gerron Smith", "role": "owner", "sources": []}


def test_declare_person_can_add_a_sensor_to_an_already_declared_person() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        service.declare_person(name="Gerron Smith", role="owner")
        result = service.declare_person(
            name="Gerron Smith", entity_id="binary_sensor.gerron_office_occupancy", room_id="office", role="owner"
        )
        assert result["ok"] is True
        person = result["setup"]["household"]["people"][0]
        assert person["role"] == "owner"
        assert person["sources"] == [{"entity_id": "binary_sensor.gerron_office_occupancy", "room_id": "office"}]


def test_declare_person_requires_entity_id_and_room_id_together() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.declare_person(name="Gerron Smith", entity_id="binary_sensor.gerron_office")
        assert result["ok"] is False
        assert "together" in result["error"]
        result = service.declare_person(name="Gerron Smith", room_id="office")
        assert result["ok"] is False
        assert "together" in result["error"]


def test_declare_context_rules_and_shared_entities() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.declare_context(label="Working late", entity_id="input_boolean.working_late")
        assert result["ok"] is True
        assert result["setup"]["household"]["contexts"] == [
            {"context_id": "working_late", "label": "Working late", "entity_id": "input_boolean.working_late"}
        ]

        # Identical re-declaration is idempotent.
        again = service.declare_context(label="Working late", entity_id="input_boolean.working_late")
        assert again["ok"] is True
        assert len(again["setup"]["household"]["contexts"]) == 1

        # A different label for the same derived id is a conflict.
        conflict = service.declare_context(label="working-late", entity_id="input_boolean.other")
        assert conflict["ok"] is False
        assert "different label" in conflict["error"]

        # A different context MAY share the same entity: one switch, several meanings.
        shared = service.declare_context(label="Focus mode", entity_id="input_boolean.working_late")
        assert shared["ok"] is True
        contexts = shared["setup"]["household"]["contexts"]
        assert len(contexts) == 2
        assert {context["entity_id"] for context in contexts} == {"input_boolean.working_late"}

        result = service.declare_context(label="Focus mode", entity_id="")
        assert result["ok"] is False


def test_remove_person_and_context() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        service.declare_person(name="Gerron", entity_id="binary_sensor.gerron_office", room_id="office")
        service.declare_context(label="Working late", entity_id="input_boolean.working_late")

        unknown = service.remove_person(person_id="nobody")
        assert unknown["ok"] is False
        unknown = service.remove_context(context_id="nothing")
        assert unknown["ok"] is False

        result = service.remove_person(person_id="gerron")
        assert result["ok"] is True
        assert result["setup"]["household"]["people"] == []
        result = service.remove_context(context_id="working_late")
        assert result["ok"] is True
        assert result["setup"]["household"]["contexts"] == []

        # The removal is persisted: a fresh service sees the empty household.
        second = _service(Path(tmp) / "data")
        assert second.status()["setup"]["household"] == {"rooms": [], "people": [], "contexts": []}


class _FakeUrlopenResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_complete_refuses_a_configured_provider_with_no_declared_owner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        with patch(
            "haven.integrations.home_assistant.client.urlopen",
            return_value=_FakeUrlopenResponse(json.dumps([]).encode("utf-8")),
        ):
            connected = service.connect_provider(kind="home_assistant", base_url="http://ha.local:8123", token="secret")
        assert connected["ok"] is True

        result = service.complete()
        assert result["ok"] is False
        assert "owner" in result["error"]
        assert service.status()["setup"]["completed"] is False

        service.declare_person(name="Ada Lovelace", entity_id="binary_sensor.ada", room_id="office", role="owner")
        result = service.complete()
        assert result["ok"] is True
        assert result["setup"]["completed"] is True


def test_complete_without_a_provider_never_requires_an_owner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        result = service.complete()
        assert result["ok"] is True
        assert result["setup"]["completed"] is True


def test_status_envelope_carries_the_household_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _service(Path(tmp) / "data")
        service.declare_person(name="Gerron", entity_id="binary_sensor.gerron_office", room_id="office")
        service.declare_context(label="Working late", entity_id="input_boolean.working_late")
        household = service.status()["setup"]["household"]
        assert household == {
            "rooms": [],
            "people": [
                {
                    "person_id": "gerron",
                    "name": "Gerron",
                    "role": "member",
                    "sources": [{"entity_id": "binary_sensor.gerron_office", "room_id": "office"}],
                }
            ],
            "contexts": [
                {"context_id": "working_late", "label": "Working late", "entity_id": "input_boolean.working_late"}
            ],
        }


def test_household_routes_round_trip_through_the_api() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _post(
                port,
                "/api/setup/household/people",
                {"name": "Gerron Smith", "entity_id": "binary_sensor.gerron_office_occupancy", "room_id": "office"},
            )
            assert status == 200
            assert body["setup"]["household"]["people"][0]["person_id"] == "gerron_smith"

            status, body = _post(port, "/api/setup/household/contexts", {"label": "Working late", "entity_id": "input_boolean.working_late"})
            assert status == 200
            assert body["setup"]["household"]["contexts"][0]["context_id"] == "working_late"

            status, body = _get_json(port, "/api/setup")
            assert status == 200
            assert len(body["setup"]["household"]["people"]) == 1
            assert len(body["setup"]["household"]["contexts"]) == 1

            status, body = _post(port, "/api/setup/household/people/remove", {"person_id": "gerron_smith"})
            assert status == 200
            assert body["setup"]["household"]["people"] == []
            status, body = _post(port, "/api/setup/household/contexts/remove", {"context_id": "working_late"})
            assert status == 200
            assert body["setup"]["household"]["contexts"] == []

            # Unknown ids and blank bodies refuse with a 400.
            status, body = _post(port, "/api/setup/household/people/remove", {"person_id": "ghost"})
            assert status == 400
            status, body = _post(port, "/api/setup/household/people", {"name": "", "entity_id": "e", "room_id": "r"})
            assert status == 400


def test_load_household_declarations_missing_and_malformed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "household.json"
        assert load_household_declarations(path) == HouseholdDeclarations()

        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(SetupConfigError):
            load_household_declarations(path)

        path.write_text(json.dumps({"version": 99, "people": [], "contexts": []}), encoding="utf-8")
        with pytest.raises(SetupConfigError):
            load_household_declarations(path)

        # A service over a malformed file still boots, on an empty household.
        path.write_text("{ not json", encoding="utf-8")
        store = SetupConfigStore(Path(tmp) / "haven.json")
        service = SetupService(store=store, director=DemoDirector(clock=lambda: NOW), clock=lambda: NOW)
        assert service.status()["setup"]["household"] == {"rooms": [], "people": [], "contexts": []}


def test_declared_presence_reaches_state_through_the_real_composition_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(
            data_dir,
            household={
                "version": 1,
                "people": [
                    {
                        "person_id": "gerron",
                        "name": "Gerron",
                        "sources": [
                            {"entity_id": "binary_sensor.gerron_office_occupancy", "room_id": "office"}
                        ],
                    }
                ],
                "contexts": [
                    {"context_id": "working_late", "label": "Working late", "entity_id": "input_boolean.working_late"}
                ],
            },
        )
        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _get_json(port, "/api/state")
            assert status == 200
            # The declared person is present in the office, by declared name.
            assert body["people"] == [{"id": "gerron", "name": "Gerron", "room": "office"}]
            office = next(room for room in body["rooms"] if room["id"] == "office")
            assert office["people"] == ["Gerron"]
            # The declared context is active, with its declared label.
            assert {"id": "working_late", "label": "Working late", "active": True} in body["contexts"]

            # The setup status exposes the same declarations.
            _, setup_body = _get_json(port, "/api/setup")
            assert setup_body["setup"]["household"]["people"][0]["person_id"] == "gerron"


def test_malformed_household_file_boots_without_people() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        (data_dir / _HOUSEHOLD_FILENAME).write_bytes(b"\x00 not json \xff")

        with _boot(data_dir, _StubStatesSource(LIVE_STATES)) as (_, _, port):
            status, body = _get_json(port, "/api/state")
            assert status == 200
            assert body["people"] == []
            assert body["contexts"] == []
            _, setup_body = _get_json(port, "/api/setup")
            assert setup_body["setup"]["household"] == {"rooms": [], "people": [], "contexts": []}
