"""Main-surface authoring: declarations and automation lifecycle.

These tests deliberately boot a real empty installation rather than the demo
director. The authoring surface must not need the fixture household in order
to create the user's rooms, people, contexts, or governed automations.
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
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _seed_installation(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    SetupConfigStore(data_dir / "haven.json").save(
        SetupConfig(completed=True, data_dir=str(data_dir), household_id="household-authoring")
    )
    (data_dir / "household.json").write_text(
        json.dumps(
            {
                "version": 1,
                "rooms": [],
                "people": [
                    {"person_id": "gerron", "name": "Gerron", "role": "owner", "sources": []}
                ],
                "contexts": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = DeviceManifest(
        device_id="light.office",
        device_type="light",
        provider_id=HA_PROVIDER_ID,
        room="office",
        capabilities=(
            CapabilityDescriptor(
                "power_off", ControlClass.LOW_RISK, readable=True, writable=True, service="light.turn_off"
            ),
        ),
    )
    (data_dir / "enrolled_devices.json").write_text(
        json.dumps({"version": 2, "manifests": [manifest.to_dict()]}),
        encoding="utf-8",
    )


@contextmanager
def _boot(data_dir: Path):
    server, _director = make_server(0, data_dir=str(data_dir), clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(port: int, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def test_real_installation_can_author_rooms_people_and_contexts_over_http() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        with _boot(data_dir) as port:
            status, body = _request(port, "POST", "/api/rooms", {"name": "Office"})
            assert status == 200
            assert body["setup"]["household"]["rooms"] == [{"room_id": "office", "name": "Office"}]

            status, state = _request(port, "GET", "/api/state")
            assert status == 200
            assert {room["id"] for room in state["rooms"]} >= {"office"}

            status, body = _request(port, "PATCH", "/api/rooms/office", {"name": "Studio"})
            assert status == 200
            assert body["setup"]["household"]["rooms"][0]["name"] == "Studio"

            status, body = _request(port, "POST", "/api/people", {"name": "Ada", "role": "member"})
            assert status == 200
            assert body["setup"]["household"]["people"][-1]["person_id"] == "ada"

            status, body = _request(port, "PATCH", "/api/people/ada", {"name": "Ada Lovelace", "role": "owner"})
            assert status == 200
            assert body["setup"]["household"]["people"][-1]["name"] == "Ada Lovelace"

            status, body = _request(
                port, "POST", "/api/contexts", {"label": "Working late", "entity_id": "input_boolean.late"}
            )
            assert status == 200
            assert body["setup"]["household"]["contexts"][0]["context_id"] == "working_late"

            status, body = _request(
                port,
                "PATCH",
                "/api/contexts/working_late",
                {"label": "Focus mode", "entity_id": "input_boolean.focus"},
            )
            assert status == 200
            assert body["setup"]["household"]["contexts"][0]["label"] == "Focus mode"

            status, body = _request(port, "DELETE", "/api/contexts/working_late")
            assert status == 200
            assert body["setup"]["household"]["contexts"] == []

            status, people = _request(port, "GET", "/api/people")
            assert status == 200
            assert {person["person_id"] for person in people["people"]} == {"gerron", "ada"}

            status, body = _request(port, "DELETE", "/api/people/ada")
            assert status == 200
            assert [person["person_id"] for person in body["setup"]["household"]["people"]] == ["gerron"]

            status, body = _request(port, "DELETE", "/api/rooms/office")
            assert status == 200
            assert body["setup"]["household"]["rooms"] == []


def test_composer_applies_declaration_proposals_through_authoring_service() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        with _boot(data_dir) as port:
            status, body = _request(port, "POST", "/api/chat", {"text": "Add an office."})
            assert status == 200
            assert body["ok"] is True
            assert body["authoring"] == {
                "entity_kind": "room",
                "operation": "create",
                "source_text": "Add an office.",
            }
            assert {row["id"] for row in body["state"]["rooms"]} >= {"office"}

            status, body = _request(
                port,
                "POST",
                "/api/chat",
                {"text": "Every weekday at 7 turn off the office light."},
            )
            assert status == 200
            assert body["authoring"]["entity_kind"] == "automation"
            assert body["automation"]["status"] == "proposed"
            assert body["automation"]["schedule"] == {
                "time_of_day": "07:00:00",
                "weekdays": [0, 1, 2, 3, 4],
            }
            rule_id = body["automation"]["rule_id"]

            status, body = _request(
                port,
                "POST",
                "/api/chat",
                {
                    "text": "Don't run that automation on Fridays.",
                    "automation_id": rule_id,
                },
            )
            assert status == 200
            assert body["automation"]["status"] == "proposed"
            assert body["automation"]["schedule"] == {
                "time_of_day": "07:00:00",
                "weekdays": [0, 1, 2, 3],
            }

            status, body = _request(
                port,
                "POST",
                "/api/chat",
                {"text": "Call this room the shop.", "focus": "office"},
            )
            assert status == 200
            assert body["state"]["rooms"]
            assert any(row["id"] == "office" and row["name"] == "shop" for row in body["state"]["rooms"])

            status, body = _request(port, "POST", "/api/chat", {"text": "Bryan lives here too."})
            assert status == 200
            # A declaration without an occupancy source is configuration, not
            # fabricated live presence.  The directory endpoint is the
            # authoritative surface for the declaration.
            status, people = _request(port, "GET", "/api/people")
            assert status == 200
            assert {row["person_id"] for row in people["people"]} == {"gerron", "bryan"}

            context_text = "Add a context called Working late using input_boolean.working_late."
            status, body = _request(port, "POST", "/api/chat", {"text": context_text})
            assert status == 200
            assert body["authoring"] == {
                "entity_kind": "context",
                "operation": "create",
                "source_text": context_text,
            }
            status, setup = _request(port, "GET", "/api/setup")
            assert status == 200
            assert setup["setup"]["household"]["contexts"] == [
                {
                    "context_id": "working_late",
                    "label": "Working late",
                    "entity_id": "input_boolean.working_late",
                }
            ]

            status, body = _request(port, "POST", "/api/chat", {"text": "Remove the shop room."})
            assert status == 200
            # The enrolled device still legitimately projects an inferred
            # `office` room; the declaration itself is what was removed.
            status, setup = _request(port, "GET", "/api/setup")
            assert status == 200
            assert setup["setup"]["household"]["rooms"] == []


def test_composer_rejects_a_scheduled_action_without_a_unique_live_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        with _boot(data_dir) as port:
            status, body = _request(
                port,
                "POST",
                "/api/chat",
                {"text": "Every weekday at 7 turn off the bedroom light."},
            )
            assert status == 400
            assert body["ok"] is False
            assert "could not find a light" in body["error"]
            status, state = _request(port, "GET", "/api/state")
            assert status == 200
            assert state["automations"] == []


def test_composer_does_not_turn_a_single_weekday_rule_into_daily_schedule() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        with _boot(data_dir) as port:
            status, body = _request(
                port,
                "POST",
                "/api/automations",
                {
                    "source_text": "Turn off the office light on Friday",
                    "time_of_day": "22:00",
                    "weekdays": [4],
                    "target_device_id": "light.office",
                    "capability": "power_off",
                    "service": "light.turn_off",
                },
            )
            assert status == 200
            rule_id = body["automation"]["rule_id"]
            status, body = _request(
                port,
                "POST",
                "/api/chat",
                {
                    "text": "Don't run that automation on Fridays.",
                    "automation_id": rule_id,
                },
            )
            assert status == 400
            assert "no scheduled weekdays" in body["error"]



def test_automation_authoring_is_proposed_edited_approved_and_revoked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        with _boot(data_dir) as port:
            status, options = _request(port, "GET", "/api/automations/options")
            assert status == 200
            assert options["options"][0]["capability"] == "power_off"

            status, body = _request(
                port,
                "POST",
                "/api/automations",
                {
                    "source_text": "Turn off the office light",
                    "time_of_day": "22:00",
                    "weekdays": [0, 2, 4],
                    "target_device_id": "light.office",
                    "capability": "power_off",
                    "service": "light.turn_off",
                },
            )
            assert status == 200
            assert body["automation"]["status"] == "proposed"
            rule_id = body["automation"]["rule_id"]
            assert body["automation"]["schedule"] == {"time_of_day": "22:00:00", "weekdays": [0, 2, 4]}

            status, body = _request(port, "PATCH", f"/api/automations/{rule_id}", {"enabled": True})
            assert status == 400
            assert "only approved automations" in body["error"]

            status, body = _request(
                port,
                "PATCH",
                f"/api/automations/{rule_id}",
                {
                    "source_text": "Turn off the office light after work",
                    "time_of_day": "22:30",
                    "weekdays": [0, 1, 2, 3, 4],
                    "justification": "The owner corrected the schedule.",
                },
            )
            assert status == 200
            assert body["ok"] is True
            assert body["automation"]["summary"] == "Turn off the office light after work"
            assert body["automation"]["schedule"] == {"time_of_day": "22:30:00", "weekdays": [0, 1, 2, 3, 4]}

            status, body = _request(
                port,
                "POST",
                f"/api/automations/{rule_id}/approve",
                {"justification": "Owner approved the corrected schedule."},
            )
            assert status == 200
            assert body["ok"] is True
            assert body["automation"]["status"] == "approved"
            assert body["automation"]["approved_by"] == "gerron"

            status, body = _request(port, "DELETE", "/api/people/gerron")
            assert status == 400
            assert "revoke approved automations" in body["error"]

            status, body = _request(
                port,
                "DELETE",
                f"/api/automations/{rule_id}",
                {"justification": "Owner no longer wants this schedule."},
            )
            assert status == 200
            assert body["ok"] is True
            assert body["automation"]["status"] == "revoked"
            assert body["automation"]["revoked_by"] == "gerron"
            assert any(row["event_type"] == "rule_revoked" for row in body["state"]["activity"])

            status, body = _request(
                port,
                "DELETE",
                f"/api/automations/{rule_id}",
                {"justification": "duplicate revoke"},
            )
            assert status == 400
            assert body["ok"] is False
            assert body["decision"]["code"] == "invalid_state_transition"


def test_automation_creation_rejects_bad_authoring_inputs_without_mutating_rules() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        with _boot(data_dir) as port:
            for payload in (
                {"source_text": "bad", "time_of_day": "not-a-time", "target_device_id": "light.office", "capability": "power_off"},
                {"source_text": "bad", "time_of_day": "22:00", "weekdays": [7], "target_device_id": "light.office", "capability": "power_off"},
                {"source_text": "bad", "time_of_day": "22:00", "target_device_id": "light.office", "capability": "does_not_exist"},
            ):
                status, body = _request(port, "POST", "/api/automations", payload)
                assert status == 400
                assert body["ok"] is False

            status, state = _request(port, "GET", "/api/state")
            assert status == 200
            assert state["automations"] == []
