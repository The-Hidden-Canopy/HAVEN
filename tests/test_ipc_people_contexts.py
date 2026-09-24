"""Native people/contexts IPC adapter: same SetupService surface the web authoring API uses."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
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


def test_people_list_mirrors_the_web_directory_shape(server) -> None:
    response = _dispatch(server, "people.list", {})

    assert response["ok"] is True
    people = response["result"]["people"]
    assert [person["person_id"] for person in people] == ["gerron"]
    row = people[0]
    assert row["name"] == "Gerron"
    assert row["role"] == "owner"
    # A declaration without an occupancy source is configuration, not
    # fabricated live presence -- same rule the web directory applies.
    assert row["present"] is False
    assert row["room"] is None


def test_people_add_update_remove_round_trip(server) -> None:
    added = _dispatch(server, "people.add", {"name": "Ada", "role": "member"})
    assert added["ok"] is True
    assert added["result"]["ok"] is True
    assert added["result"]["setup"]["household"]["people"][-1]["person_id"] == "ada"

    updated = _dispatch(
        server, "people.update", {"person_id": "ada", "name": "Ada Lovelace", "role": "owner"}
    )
    assert updated["ok"] is True
    assert updated["result"]["ok"] is True
    assert updated["result"]["setup"]["household"]["people"][-1]["name"] == "Ada Lovelace"
    assert updated["result"]["setup"]["household"]["people"][-1]["role"] == "owner"

    removed = _dispatch(server, "people.remove", {"person_id": "ada"})
    assert removed["ok"] is True
    assert [
        person["person_id"] for person in removed["result"]["setup"]["household"]["people"]
    ] == ["gerron"]


def test_people_add_validation_errors_match_the_web_surface(server) -> None:
    blank = _dispatch(server, "people.add", {"name": "  "})
    assert blank["ok"] is True
    assert blank["result"]["ok"] is False
    assert "name" in blank["result"]["error"]

    unpaired = _dispatch(
        server,
        "people.add",
        {"name": "Ada", "entity_id": "device_tracker.ada", "room_id": ""},
    )
    assert unpaired["result"]["ok"] is False
    assert "entity_id and room_id must be given together" in unpaired["result"]["error"]

    bad_role = _dispatch(server, "people.add", {"name": "Ada", "role": "admin"})
    assert bad_role["result"]["ok"] is False
    assert "role must be one of" in bad_role["result"]["error"]

    # The web defaults an absent/blank role to "member".
    defaulted = _dispatch(server, "people.add", {"name": "Grace Hopper", "role": "  "})
    assert defaulted["result"]["ok"] is True
    assert defaulted["result"]["setup"]["household"]["people"][-1]["role"] == "member"

    # Re-declaring the same person under a different role updates the role.
    promoted = _dispatch(server, "people.add", {"name": "Grace Hopper", "role": "owner"})
    assert promoted["result"]["ok"] is True
    assert promoted["result"]["setup"]["household"]["people"][-1]["role"] == "owner"

    # The same derived person_id under a different name is a conflict.
    conflict = _dispatch(server, "people.add", {"name": "grace hopper"})
    assert conflict["result"]["ok"] is False
    assert "already declared with a different name" in conflict["result"]["error"]


def test_people_update_and_remove_refuse_unknown_ids(server) -> None:
    updated = _dispatch(server, "people.update", {"person_id": "ada", "name": "Ada"})
    assert updated["ok"] is True
    assert updated["result"]["ok"] is False
    assert updated["result"]["error"] == "unknown person: ada"

    removed = _dispatch(server, "people.remove", {"person_id": "ada"})
    assert removed["ok"] is True
    assert removed["result"]["ok"] is False
    assert removed["result"]["error"] == "unknown person: ada"


def test_contexts_list_starts_empty_and_mirrors_the_web_shape(server) -> None:
    response = _dispatch(server, "contexts.list", {})

    assert response["ok"] is True
    assert response["result"]["contexts"] == []


def test_contexts_add_update_remove_round_trip(server) -> None:
    added = _dispatch(
        server, "contexts.add", {"label": "Working late", "entity_id": "input_boolean.late"}
    )
    assert added["ok"] is True
    assert added["result"]["ok"] is True
    assert added["result"]["setup"]["household"]["contexts"][0]["context_id"] == "working_late"

    listed = _dispatch(server, "contexts.list", {})
    assert listed["result"]["contexts"][0]["label"] == "Working late"
    assert listed["result"]["contexts"][0]["entity_id"] == "input_boolean.late"
    assert listed["result"]["contexts"][0]["active"] is False

    updated = _dispatch(
        server,
        "contexts.update",
        {"context_id": "working_late", "label": "Focus mode", "entity_id": "input_boolean.focus"},
    )
    assert updated["ok"] is True
    assert updated["result"]["ok"] is True
    assert updated["result"]["setup"]["household"]["contexts"][0]["label"] == "Focus mode"

    removed = _dispatch(server, "contexts.remove", {"context_id": "working_late"})
    assert removed["ok"] is True
    assert removed["result"]["setup"]["household"]["contexts"] == []


def test_contexts_validation_errors_match_the_web_surface(server) -> None:
    missing_entity = _dispatch(server, "contexts.add", {"label": "Working late"})
    assert missing_entity["ok"] is True
    assert missing_entity["result"]["ok"] is False
    assert "entity_id" in missing_entity["result"]["error"]

    conflict = _dispatch(
        server, "contexts.add", {"label": "Working late", "entity_id": "input_boolean.late"}
    )
    assert conflict["result"]["ok"] is True
    # Re-declaring the same context with a different entity updates the entity.
    rebound = _dispatch(
        server, "contexts.add", {"label": "Working late", "entity_id": "input_boolean.focus"}
    )
    assert rebound["result"]["ok"] is True
    assert rebound["result"]["setup"]["household"]["contexts"][0]["entity_id"] == "input_boolean.focus"
    # The same derived context_id under a different label is a conflict.
    conflict_again = _dispatch(
        server, "contexts.add", {"label": "working late", "entity_id": "input_boolean.late"}
    )
    assert conflict_again["result"]["ok"] is False
    assert "already declared with a different label" in conflict_again["result"]["error"]

    updated = _dispatch(
        server, "contexts.update", {"context_id": "night", "label": "Night"}
    )
    assert updated["result"]["ok"] is False
    assert updated["result"]["error"] == "unknown context: night"

    removed = _dispatch(server, "contexts.remove", {"context_id": "night"})
    assert removed["result"]["ok"] is False
    assert removed["result"]["error"] == "unknown context: night"
