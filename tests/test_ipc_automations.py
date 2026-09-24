"""Native automations IPC adapter: the full propose/clarify/approve/enable/revoke lifecycle."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.devices import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.ipc import request_message
from haven.web.application import HA_PROVIDER_ID
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

CREATE_OFFICE = {
    "source_text": "Turn off the office light",
    "time_of_day": "22:00",
    "weekdays": [0, 2, 4],
    "target_device_id": "light.office",
    "capability": "power_off",
    "service": "light.turn_off",
}


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


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_installation(data_dir)
        instance, _director = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            yield instance
        finally:
            instance.server_close()


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def test_options_list_and_create_mirror_the_web_surface(server) -> None:
    options = _dispatch(server, "automations.options", {})
    assert options["ok"] is True
    assert options["result"]["options"][0]["capability"] == "power_off"
    assert options["result"]["options"][0]["service"] == "light.turn_off"

    created = _dispatch(server, "automations.create", CREATE_OFFICE)
    assert created["ok"] is True
    automation = created["result"]["automation"]
    assert automation["status"] == "proposed"
    assert automation["schedule"] == {"time_of_day": "22:00:00", "weekdays": [0, 2, 4]}
    assert automation["target"] == "light.office · Office"

    listed = _dispatch(server, "automations.list", {})
    assert listed["ok"] is True
    assert listed["result"]["automations"] == created["result"]["state"]["automations"]
    assert "scheduler" in listed["result"]


def test_proposed_rule_is_editable_then_freezes_on_approval(server) -> None:
    rule_id = _dispatch(server, "automations.create", CREATE_OFFICE)["result"]["automation"]["rule_id"]

    edited = _dispatch(
        server,
        "automations.update",
        {
            "rule_id": rule_id,
            "source_text": "Turn off the office light after work",
            "time_of_day": "22:30",
            "weekdays": [0, 1, 2, 3, 4],
            "justification": "The owner corrected the schedule.",
        },
    )
    assert edited["ok"] is True
    assert edited["result"]["ok"] is True
    assert edited["result"]["automation"]["summary"] == "Turn off the office light after work"
    assert edited["result"]["automation"]["schedule"] == {
        "time_of_day": "22:30:00",
        "weekdays": [0, 1, 2, 3, 4],
    }

    approved = _dispatch(server, "automations.approve", {"rule_id": rule_id})
    assert approved["ok"] is True
    assert approved["result"]["ok"] is True
    assert approved["result"]["automation"]["status"] == "approved"
    assert approved["result"]["automation"]["approved_by"] == "gerron"

    frozen = _dispatch(
        server,
        "automations.update",
        {
            "rule_id": rule_id,
            "source_text": "Change everything",
            "time_of_day": "23:00",
            "weekdays": [],
        },
    )
    assert frozen["result"]["ok"] is False
    assert "only proposed automations can be edited" in frozen["result"]["error"]


def test_enable_is_gated_on_approval_and_toggles_scheduling(server) -> None:
    rule_id = _dispatch(server, "automations.create", CREATE_OFFICE)["result"]["automation"]["rule_id"]

    early = _dispatch(server, "automations.enable", {"rule_id": rule_id, "enabled": True})
    assert early["ok"] is False
    assert "only approved automations" in early["error"]

    _dispatch(server, "automations.approve", {"rule_id": rule_id})
    enabled = _dispatch(server, "automations.enable", {"rule_id": rule_id, "enabled": True})
    assert enabled["ok"] is True
    rows = {row["rule_id"]: row for row in enabled["result"]["scheduler"]}
    assert rows[rule_id]["enabled"] is True

    disabled = _dispatch(server, "automations.enable", {"rule_id": rule_id, "enabled": False})
    assert disabled["ok"] is True
    rows = {row["rule_id"]: row for row in disabled["result"]["scheduler"]}
    assert rows[rule_id]["enabled"] is False

    missing = _dispatch(server, "automations.enable", {"rule_id": "rule:nope", "enabled": True})
    assert missing["ok"] is False
    assert missing["error"] == "unknown automation"


def test_revoke_requires_a_justification_and_is_fail_closed(server) -> None:
    rule_id = _dispatch(server, "automations.create", CREATE_OFFICE)["result"]["automation"]["rule_id"]
    _dispatch(server, "automations.approve", {"rule_id": rule_id})

    blank = _dispatch(server, "automations.revoke", {"rule_id": rule_id, "justification": "  "})
    assert blank["ok"] is False
    assert "justification" in blank["error"]

    revoked = _dispatch(
        server,
        "automations.revoke",
        {"rule_id": rule_id, "justification": "Owner no longer wants this schedule."},
    )
    assert revoked["ok"] is True
    assert revoked["result"]["ok"] is True
    assert revoked["result"]["automation"]["status"] == "revoked"
    assert revoked["result"]["automation"]["revoked_by"] == "gerron"
    assert any(
        row["event_type"] == "rule_revoked" for row in revoked["result"]["state"]["activity"]
    )

    again = _dispatch(
        server,
        "automations.revoke",
        {"rule_id": rule_id, "justification": "duplicate revoke"},
    )
    assert again["ok"] is True
    assert again["result"]["ok"] is False
    assert again["result"]["decision"]["code"] == "invalid_state_transition"

    unknown = _dispatch(
        server, "automations.revoke", {"rule_id": "rule:nope", "justification": "x"}
    )
    assert unknown["result"]["ok"] is False
    assert unknown["result"]["error"] == "unknown automation"


def test_bad_authoring_inputs_create_no_rules(server) -> None:
    for payload in (
        {"source_text": "bad", "time_of_day": "not-a-time", "target_device_id": "light.office", "capability": "power_off"},
        {"source_text": "bad", "time_of_day": "22:00", "weekdays": [7], "target_device_id": "light.office", "capability": "power_off"},
        {"source_text": "bad", "time_of_day": "22:00", "target_device_id": "light.office", "capability": "does_not_exist"},
    ):
        response = _dispatch(server, "automations.create", payload)
        assert response["ok"] is True
        assert response["result"]["ok"] is False

    listed = _dispatch(server, "automations.list", {})
    assert listed["result"]["automations"] == []
