"""Native system IPC adapter: diagnostics, probe, backup lifecycle, service control."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.web.server import make_server
from haven.web.service_manager import ServiceManager

from test_web_diagnostics import (
    LIVE_STATES,
    _RaisingStatesSource,
    _StubStatesSource,
    _seed_real_setup,
)
from test_web_service import FakeRunner, _query_result


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


@pytest.fixture()
def real_server():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        instance, director = make_server(
            0, data_dir=data_dir, ha_client=_StubStatesSource(LIVE_STATES)
        )
        try:
            yield instance, director, data_dir
        finally:
            instance.server_close()


def test_diagnostics_shape_in_real_and_demo_and_local_modes(real_server) -> None:
    instance, _, data_dir = real_server
    response = _dispatch(instance, "system.diagnostics", {})

    assert response["ok"] is True
    diag = response["result"]["diagnostics"]
    assert set(diag) == {
        "data_dir", "config_error", "world", "provider", "household", "devices",
        "rules", "scheduler", "events", "knowledge", "models", "voice", "uptime_seconds",
    }
    assert diag["data_dir"] == str(data_dir)
    assert diag["world"] == {"mode": "home_assistant"}
    assert diag["provider"] == {
        "configured": True,
        "kind": "home_assistant",
        "base_url": "http://ha.local:8123",
    }
    assert diag["rules"]["total"] == diag["rules"]["approved"] + diag["rules"]["proposed"]
    assert diag["voice"]["enabled"] is False
    assert diag["uptime_seconds"] >= 0

    with tempfile.TemporaryDirectory() as tmp:
        demo_dir = Path(tmp) / "data"
        demo_dir.mkdir(parents=True)
        demo_instance, demo_director = make_server(0, data_dir=demo_dir, demo=True)
        try:
            assert demo_director.house is not None
            diag = _dispatch(demo_instance, "system.diagnostics", {})["result"]["diagnostics"]
            assert diag["world"] == {"mode": "demo"}
            assert diag["devices"]["registered"] == 5
            assert diag["rules"]["approved"] == 3
            assert diag["scheduler"]["enabled"] == 1
        finally:
            demo_instance.server_close()

    with tempfile.TemporaryDirectory() as tmp:
        local_dir = Path(tmp) / "data"
        local_instance, local_director = make_server(0, data_dir=local_dir)
        try:
            assert local_director.house is None
            diag = _dispatch(local_instance, "system.diagnostics", {})["result"]["diagnostics"]
            assert diag["world"] == {"mode": "local"}
            assert diag["devices"] == {"enrolled": 0, "registered": 0}
        finally:
            local_instance.server_close()


def test_probe_provider_reachable_unreachable_and_absent(real_server) -> None:
    instance, _, _ = real_server
    reachable = _dispatch(instance, "system.probe", {})
    assert reachable["result"]["ok"] is True
    assert reachable["result"]["reachable"] is True

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        _seed_real_setup(data_dir)
        down, _ = make_server(0, data_dir=data_dir, ha_client=_RaisingStatesSource())
        try:
            unreachable = _dispatch(down, "system.probe", {})
            assert unreachable["result"]["ok"] is True
            assert unreachable["result"]["reachable"] is False
            assert "ConnectionError" in unreachable["result"]["detail"]
        finally:
            down.server_close()

    with tempfile.TemporaryDirectory() as tmp:
        bare, _ = make_server(0, data_dir=Path(tmp) / "data")
        try:
            absent = _dispatch(bare, "system.probe", {})
            assert absent["result"] == {"ok": False, "error": "no provider attached"}
        finally:
            bare.server_close()


def test_backup_lifecycle_create_list_restore_delete(real_server) -> None:
    instance, _, data_dir = real_server
    preferences = instance.setup.set_preferences(voice=True, intelligence=False)
    assert preferences["ok"] is True
    instance.setup.declare_person(
        name="Gerron Smith",
        entity_id="binary_sensor.gerron_office_occupancy",
        room_id="office",
    )

    first = _dispatch(instance, "system.backup.create", {})
    assert first["result"]["ok"] is True
    first_id = first["result"]["backup"]["id"]
    assert {"haven.json", "household.json", "enrolled_devices.json"} <= set(
        first["result"]["backup"]["files"]
    )

    instance.setup.set_preferences(voice=False, intelligence=False)
    second = _dispatch(instance, "system.backup.create", {})
    second_id = second["result"]["backup"]["id"]

    listed = _dispatch(instance, "system.backups", {})
    assert [entry["id"] for entry in listed["result"]["backups"]] == sorted(
        [first_id, second_id], reverse=True
    )

    # Path traversal and unknown ids refuse fail-closed, like the web 400s.
    for bad in ("../x", "a/b", "..", "ghost"):
        restored = _dispatch(instance, "system.backup.restore", {"id": bad})
        assert restored["ok"] is False
        deleted = _dispatch(instance, "system.backup.delete", {"id": bad})
        assert deleted["ok"] is False
    blank = _dispatch(instance, "system.backup.restore", {"id": "  "})
    assert blank["ok"] is False
    assert "id" in blank["error"]

    restored = _dispatch(instance, "system.backup.restore", {"id": first_id})
    assert restored["result"]["ok"] is True
    assert restored["result"]["result"]["restart_required"] is True
    assert "haven.json" in restored["result"]["result"]["restored"]
    saved = json.loads((data_dir / "haven.json").read_text(encoding="utf-8"))
    assert saved["voice_enabled"] is True

    deleted = _dispatch(instance, "system.backup.delete", {"id": first_id})
    assert deleted["result"]["ok"] is True
    listed = _dispatch(instance, "system.backups", {})
    assert [entry["id"] for entry in listed["result"]["backups"]] == [second_id]

    # The restore is real: a fresh boot on the same data dir reads era one.
    rebound, _ = make_server(0, data_dir=data_dir, ha_client=_StubStatesSource(LIVE_STATES))
    try:
        status = rebound.setup.status()
        assert status["setup"]["preferences"]["voice"] is True
    finally:
        rebound.server_close()


def test_service_status_install_and_uninstall(real_server) -> None:
    from test_web_service import _Completed

    instance, _, data_dir = real_server
    calls: list[list[str]] = []

    def recording_runner(args: list[str]):
        calls.append(list(args))
        if "/Query" in args:
            return _Completed(0, stdout=_query_result("Ready").stdout)
        return _Completed(0)

    def swap(result_runner) -> None:
        instance.service = ServiceManager(
            data_dir=data_dir,
            port_getter=lambda: instance.server_address[1],
            runner=result_runner,
        )

    swap(recording_runner)
    status = _dispatch(instance, "system.service", {})
    assert status["result"] == {
        "ok": True,
        "service": {"installed": True, "running": False, "detail": "Ready"},
    }

    installed = _dispatch(instance, "system.service.install", {})
    assert installed["result"]["ok"] is True
    command = calls[-1][-1]
    assert f"--port {instance.server_address[1]}" in command
    assert f"HAVEN_DATA_DIR={data_dir}" in command

    swap(FakeRunner(_Completed(1, stderr="ERROR: Access is denied.\n")))
    refused = _dispatch(instance, "system.service.install", {})
    assert refused["result"] == {"ok": False, "error": "ERROR: Access is denied."}

    swap(recording_runner)
    removed = _dispatch(instance, "system.service.uninstall", {})
    assert removed["result"]["ok"] is True
    assert calls[-1][:2] == ["schtasks", "/Delete"]

    swap(FakeRunner(OSError("no scheduler here")))
    unavailable = _dispatch(instance, "system.service", {})
    assert unavailable["result"]["service"]["detail"] == "service management unavailable on this platform"
