"""Native IPC surface for durable, domain-independent resource automations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from haven.ipc import request_message
from haven.web.server import make_server


NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _spec(*, household_id: str = "household-a") -> dict:
    return {
        "spec_id": "spec-1",
        "household_id": household_id,
        "trigger": {
            "kind": "event",
            "parameters": [["event_name", "task.status_changed"], ["to_state", "done"]],
        },
        "selector": {"parameters": []},
        "action": {
            "domain": "computer",
            "action": "computer.noop",
            "consequence_class": "reversible_local",
            "parameters": [["resource_id", "file-1"]],
        },
        "source_text": "when the task is done, perform the local action",
        "created_by": "owner-1",
    }


def test_resource_automation_ipc_lifecycle_and_restart(tmp_path: Path):
    server, _ = make_server(0, data_dir=tmp_path / "data", clock=lambda: NOW)
    try:
        created = _dispatch(
            server,
            "resource_automations.create",
            {
                "rule_id": "resource-rule-1",
                "spec": _spec(household_id=server.director.household_id),
            },
        )
        assert created["ok"] is True
        assert created["result"]["automation"]["status"] == "proposed"

        approved = _dispatch(
            server,
            "resource_automations.approve",
            {"rule_id": "resource-rule-1", "justification": "approve the governed resource rule"},
        )
        assert approved["ok"] is True
        assert approved["result"]["automation"]["status"] == "approved"
        assert approved["result"]["audit_event"]["transition"] == "approve"

        listed = _dispatch(server, "resource_automations.list", {})
        assert listed["ok"] is True
        assert listed["result"]["automations"][0]["rule_id"] == "resource-rule-1"

        paused = _dispatch(
            server,
            "resource_automations.enable",
            {
                "rule_id": "resource-rule-1",
                "enabled": False,
                "justification": "pause while the provider is unavailable",
            },
        )
        assert paused["ok"] is True
        assert paused["result"]["automation"]["enabled"] is False
    finally:
        server.server_close()

    restored, _ = make_server(0, data_dir=tmp_path / "data", clock=lambda: NOW)
    try:
        listed = _dispatch(restored, "resource_automations.list", {})
        assert listed["result"]["automations"][0]["status"] == "approved"
        assert listed["result"]["automations"][0]["enabled"] is False
        assert len(restored.resource_automations.lifecycle_events()) == 2
    finally:
        restored.server_close()


def test_resource_automation_rejects_foreign_household_spec(tmp_path: Path):
    server, _ = make_server(0, data_dir=tmp_path / "data", clock=lambda: NOW)
    try:
        payload = _spec()
        payload["household_id"] = "foreign-household"
        result = _dispatch(server, "resource_automations.create", {"rule_id": "foreign", "spec": payload})
        assert result["ok"] is False
    finally:
        server.server_close()


def test_task_deadline_reaches_the_durable_ipc_scheduler(tmp_path: Path):
    server, _ = make_server(0, data_dir=tmp_path / "data", clock=lambda: NOW)
    try:
        principal = server.identity.current_principal()
        created_task = server.tasks_service.create(
            server.identity.visible_scope_ids(),
            scope_id=server.identity.personal_scope_id,
            title="Deadline-backed action",
            created_by=principal.actor_id,
            due_at=NOW - timedelta(minutes=1),
        )
        assert created_task["ok"] is True
        task_id = created_task["task"]["task_id"]
        payload = _spec(household_id=server.director.household_id)
        payload["trigger"] = {
            "kind": "deadline",
            "parameters": [["source_kind", "task"], ["source_id", task_id]],
        }
        created = _dispatch(
            server,
            "resource_automations.create",
            {"rule_id": "deadline-rule", "spec": payload},
        )
        assert created["ok"] is True
        approved = _dispatch(
            server,
            "resource_automations.approve",
            {"rule_id": "deadline-rule", "justification": "approve the deadline test"},
        )
        assert approved["ok"] is True

        ticked = _dispatch(server, "resource_automations.tick", {})
        assert ticked["ok"] is True
        assert [row["rule_id"] for row in ticked["result"]["outcomes"]] == ["deadline-rule"]
    finally:
        server.server_close()
