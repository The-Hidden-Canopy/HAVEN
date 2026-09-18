"""Logon-startup management (Task Scheduler) over the web surface.

The ServiceManager talks to `schtasks.exe` through an injectable runner, so
tests never touch the real Task Scheduler: a fake runner cans the query
output and records the create/delete argv. The HTTP tests additionally prove
that the launch command is built lazily — a server bound to ephemeral port 0
installs a task carrying the real bound port.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from haven.web.server import make_server
from haven.web.service_manager import TASK_NAME, ServiceManager


class _Completed:
    """The slice of subprocess.CompletedProcess the manager reads."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Cans schtasks answers and records every argv it was asked to run."""

    def __init__(self, result: _Completed | Exception) -> None:
        self._result = result
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> _Completed:
        self.calls.append(list(args))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _query_result(status_word: str | None) -> _Completed:
    stdout = "Folder: \\\nHostName:     MACHINE\nTaskName:     \\HAVEN\n"
    if status_word is not None:
        stdout += f"Status:           {status_word}\n"
    return _Completed(0, stdout=stdout)


@contextmanager
def _boot(data_dir: Path, runner: FakeRunner):
    instance, _ = make_server(0, data_dir=str(data_dir))
    # Swap in the fake through the public constructor seam: same data dir,
    # lazy port getter, canned runner.
    instance.service = ServiceManager(
        data_dir=data_dir,
        port_getter=lambda: instance.server_address[1],
        runner=runner,
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
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


def _post(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, body="", headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def test_status_installed_and_running() -> None:
    runner = FakeRunner(_query_result("Running"))
    manager = ServiceManager(data_dir=Path("x"), port_getter=lambda: 8080, runner=runner)
    assert manager.status() == {"installed": True, "running": True, "detail": "Running"}
    assert runner.calls == [["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"]]


def test_status_installed_not_running() -> None:
    manager = ServiceManager(
        data_dir=Path("x"), port_getter=lambda: 8080, runner=FakeRunner(_query_result("Ready"))
    )
    assert manager.status() == {"installed": True, "running": False, "detail": "Ready"}


def test_status_not_installed_uses_stderr_tail() -> None:
    runner = FakeRunner(_Completed(1, stderr="ERROR: The system cannot find the file specified.\n"))
    manager = ServiceManager(data_dir=Path("x"), port_getter=lambda: 8080, runner=runner)
    assert manager.status() == {
        "installed": False,
        "running": False,
        "detail": "ERROR: The system cannot find the file specified.",
    }
    runner = FakeRunner(_Completed(1, stderr=""))
    manager = ServiceManager(data_dir=Path("x"), port_getter=lambda: 8080, runner=runner)
    assert manager.status()["detail"] == "not installed"


def test_status_runner_missing_is_unavailable_not_raised() -> None:
    manager = ServiceManager(
        data_dir=Path("x"), port_getter=lambda: 8080, runner=FakeRunner(FileNotFoundError("schtasks"))
    )
    assert manager.status() == {
        "installed": False,
        "running": False,
        "detail": "service management unavailable on this platform",
    }


def test_install_command_pins_non_default_data_dir() -> None:
    runner = FakeRunner(_Completed(0))
    data_dir = Path("E:/HiddenCanopy/custom-data")
    manager = ServiceManager(data_dir=data_dir, port_getter=lambda: 8123, runner=runner)
    result = manager.install()
    assert result == {"ok": True, "detail": "HAVEN will start at logon on port 8123"}
    args = runner.calls[0]
    assert args[:8] == ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "ONLOGON", "/RL"]
    assert args[8] == "LIMITED" and args[9] == "/TR"
    command = args[10]
    assert f"set HAVEN_DATA_DIR={data_dir}&&" in command
    assert f'"{sys.executable}"' in command
    assert "-m haven.web.server" in command
    assert "--port 8123" in command


def test_install_command_default_data_dir_is_bare() -> None:
    runner = FakeRunner(_Completed(0))
    env = dict(os.environ)
    env.pop("HAVEN_DATA_DIR", None)
    with mock.patch.dict(os.environ, env, clear=True):
        manager = ServiceManager(
            data_dir=Path.home() / ".haven", port_getter=lambda: 8080, runner=runner
        )
        manager.install()
    command = runner.calls[0][-1]
    assert "HAVEN_DATA_DIR" not in command
    assert "-m haven.web.server --port 8080" in command


def test_install_failure_maps_stderr() -> None:
    runner = FakeRunner(_Completed(1, stderr="ERROR: Access is denied.\n"))
    manager = ServiceManager(data_dir=Path("x"), port_getter=lambda: 8080, runner=runner)
    assert manager.install() == {"ok": False, "error": "ERROR: Access is denied."}


def test_uninstall_args_and_returncode_mapping() -> None:
    runner = FakeRunner(_Completed(0))
    manager = ServiceManager(data_dir=Path("x"), port_getter=lambda: 8080, runner=runner)
    assert manager.uninstall() == {"ok": True, "detail": "removed"}
    assert "/Delete" in runner.calls[0]

    runner = FakeRunner(_Completed(1, stderr="ERROR: The system cannot find the file specified.\n"))
    manager = ServiceManager(data_dir=Path("x"), port_getter=lambda: 8080, runner=runner)
    assert manager.uninstall() == {"ok": False, "error": "ERROR: The system cannot find the file specified."}


def test_status_route_over_http() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        runner = FakeRunner(_query_result("Ready"))
        with _boot(data_dir, runner) as (_, port):
            status, body = _get_json(port, "/api/system/service")
            assert status == 200
            assert body["ok"] is True
            assert body["service"] == {"installed": True, "running": False, "detail": "Ready"}


def test_install_route_uses_ephemeral_bound_port() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        runner = FakeRunner(_Completed(0))
        with _boot(data_dir, runner) as (instance, port):
            assert port != 0
            status, body = _post(port, "/api/system/service/install")
            assert status == 200
            assert body["ok"] is True
            command = runner.calls[0][-1]
            assert f"--port {port}" in command
            assert f"HAVEN_DATA_DIR={data_dir}" in command


def test_install_route_failure_is_400() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        runner = FakeRunner(_Completed(1, stderr="ERROR: Access is denied.\n"))
        with _boot(data_dir, runner) as (_, port):
            status, body = _post(port, "/api/system/service/install")
            assert status == 400
            assert body == {"ok": False, "error": "ERROR: Access is denied."}


def test_uninstall_route_over_http() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        runner = FakeRunner(_Completed(0))
        with _boot(data_dir, runner) as (_, port):
            status, body = _post(port, "/api/system/service/uninstall")
            assert status == 200
            assert body == {"ok": True, "detail": "removed"}
            assert runner.calls[0][:2] == ["schtasks", "/Delete"]


def test_platform_absent_route_still_answers_200() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        runner = FakeRunner(OSError("no scheduler here"))
        with _boot(data_dir, runner) as (_, port):
            status, body = _get_json(port, "/api/system/service")
            assert status == 200
            assert body["ok"] is True
            assert body["service"] == {
                "installed": False,
                "running": False,
                "detail": "service management unavailable on this platform",
            }
            status, body = _post(port, "/api/system/service/install")
            assert status == 400
            assert body["ok"] is False
