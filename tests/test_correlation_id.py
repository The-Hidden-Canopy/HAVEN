"""One correlation id threaded native action -> IPC -> receipt/history
(native product-consolidation plan, P0 "Runtime" instrumentation)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.core import correlation
from haven.ipc import request_message
from haven.web.server import make_server

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            yield instance
        finally:
            instance.server_close()


def test_current_is_none_outside_any_bound_scope():
    assert correlation.current() is None


def test_bind_scopes_current_and_restores_on_exit():
    assert correlation.current() is None
    with correlation.bind("corr-abc"):
        assert correlation.current() == "corr-abc"
        with correlation.bind("corr-nested"):
            assert correlation.current() == "corr-nested"
        assert correlation.current() == "corr-abc"
    assert correlation.current() is None


def test_ipc_request_id_reaches_the_computer_action_ledger_as_correlation_id(server, tmp_path) -> None:
    allowed = tmp_path / "Documents"
    allowed.mkdir()

    dispatcher = server.build_ipc_dispatcher()
    dispatcher(request_message("setup-root", "setup.computer.roots.add", {"path": str(allowed)}))
    dispatcher(request_message("setup-enable", "setup.computer", {"enabled": True, "read_only": False}))
    dispatcher(request_message("setup-owner", "setup.household.people.add", {"name": "Gerron Smith", "role": "owner"}))

    my_request_id = "req-correlate-me-123"
    result = dispatcher(
        request_message(
            my_request_id,
            "computer.action.request",
            {
                "action": "filesystem.create_folder",
                "parameters": {"path": str(allowed / "Project Files")},
                "justification": "user requested via System > Files",
            },
        )
    )
    assert result["request_id"] == my_request_id
    assert result["result"]["success"] is True

    history = dispatcher(request_message("history", "computer.action.history", {}))["result"]
    assert history["entries"][0]["correlation_id"] == my_request_id


def test_correlation_id_is_not_leaked_across_unrelated_requests(server, tmp_path) -> None:
    allowed = tmp_path / "Documents"
    allowed.mkdir()
    dispatcher = server.build_ipc_dispatcher()
    dispatcher(request_message("setup-root", "setup.computer.roots.add", {"path": str(allowed)}))
    dispatcher(request_message("setup-enable", "setup.computer", {"enabled": True, "read_only": False}))
    dispatcher(request_message("setup-owner", "setup.household.people.add", {"name": "Gerron Smith", "role": "owner"}))

    dispatcher(
        request_message(
            "req-1",
            "computer.action.request",
            {"action": "filesystem.create_folder", "parameters": {"path": str(allowed / "A")}, "justification": "a"},
        )
    )
    dispatcher(
        request_message(
            "req-2",
            "computer.action.request",
            {"action": "filesystem.create_folder", "parameters": {"path": str(allowed / "B")}, "justification": "b"},
        )
    )

    history = dispatcher(request_message("history", "computer.action.history", {}))["result"]
    by_correlation = {entry["correlation_id"] for entry in history["entries"]}
    assert by_correlation == {"req-1", "req-2"}
    assert correlation.current() is None  # nothing leaks outside the dispatcher's own scope
