"""Desktop hosting is testable without launching a real browser window."""

from __future__ import annotations

import http.client
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from haven.desktop.shell import (
    DesktopShell,
    DesktopShellAlreadyRunning,
    _read_activation_record,
    find_edge_executable,
)
from haven.desktop.instance_lock import InstanceAlreadyRunning, InstanceLock


def test_repository_windows_launcher_uses_desktop_as_the_primary_host():
    launcher = (Path(__file__).parents[1] / "run-haven.bat").read_text(encoding="utf-8")

    assert "%PY% -m haven.desktop %*" in launcher
    assert "%PY% -m haven.web.server" not in launcher


class _FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.stdin = _FakeStdin() if kwargs.get("stdin") is not None else None
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class _FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, value):
        self.data.extend(value)
        return len(value)

    def flush(self):
        return None

    def close(self):
        self.closed = True


def _bootstrap_status(url: str) -> int:
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request("GET", parsed.path + "?" + parsed.query)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_shell_starts_loopback_server_and_edge_app_mode_with_a_fresh_session():
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "msedge.exe"
        edge.write_bytes(b"test executable placeholder")
        processes = []

        def launch(args, **kwargs):
            process = _FakeProcess(args, **kwargs)
            processes.append(process)
            return process

        shell = DesktopShell(
            data_dir=Path(tmp) / "data",
            demo=True,
            edge_path=edge,
            process_factory=launch,
        )
        shell.start()
        try:
            assert shell.bootstrap_url.startswith("http://127.0.0.1:")
            assert "/__desktop_bootstrap?session=" in shell.bootstrap_url
            assert shell.session_token
            assert shell.bootstrap_token
            assert shell.bootstrap_token != shell.session_token
            assert len(processes) == 1
            assert f"--app={shell.bootstrap_url}" in processes[0].args
            assert any(arg.startswith("--user-data-dir=") for arg in processes[0].args)
            assert "--new-window" in processes[0].args
        finally:
            shell.close()
        assert processes[0].terminated is True


def test_edge_path_environment_override_is_used_only_when_it_exists(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "edge.exe"
        edge.write_bytes(b"test executable placeholder")
        monkeypatch.setenv("HAVEN_EDGE_PATH", str(edge))
        assert find_edge_executable() == edge


def test_second_shell_for_the_same_data_dir_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "msedge.exe"
        edge.write_bytes(b"test executable placeholder")
        processes = []

        def launch(args):
            process = _FakeProcess(args)
            processes.append(process)
            return process

        first = DesktopShell(
            data_dir=Path(tmp) / "data",
            demo=True,
            edge_path=edge,
            process_factory=launch,
        )
        second = DesktopShell(
            data_dir=Path(tmp) / "data",
            demo=True,
            edge_path=edge,
            process_factory=launch,
        )
        first.start()
        try:
            with pytest.raises(DesktopShellAlreadyRunning):
                second.start()
            assert len(processes) == 1
        finally:
            second.close()
            first.close()


def test_second_shell_requests_activation_before_exiting():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        activation_requests = []
        first = DesktopShell(
            data_dir=data_dir,
            demo=True,
            background=True,
            port=0,
            window_activator=lambda: activation_requests.append(True) or True,
        )
        second = DesktopShell(data_dir=data_dir, demo=True, background=True, port=0)
        first.start()
        try:
            with pytest.raises(DesktopShellAlreadyRunning, match="activation requested"):
                second.start()
            assert activation_requests == [True]
        finally:
            second.close()
            first.close()


def test_background_activation_creates_a_window_and_reopens_after_close():
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "msedge.exe"
        edge.write_bytes(b"test executable placeholder")
        processes = []

        def launch(args):
            process = _FakeProcess(args)
            processes.append(process)
            return process

        shell = DesktopShell(
            data_dir=Path(tmp) / "data",
            demo=True,
            background=True,
            port=0,
            edge_path=edge,
            process_factory=launch,
        )
        shell.start()
        try:
            assert shell.process is None
            assert shell._activate_existing_window() is True
            assert len(processes) == 1
            first_process = processes[0]
            assert shell.process is first_process
            assert shell.bootstrap_url is not None

            # The resident server remains alive after the app window closes.
            first_process.returncode = 0
            assert shell._activate_existing_window() is True
            assert len(processes) == 2
            assert shell.process is processes[1]
            assert shell.process is not first_process
        finally:
            shell.close()


def test_recreated_desktop_window_gets_a_fresh_http_bootstrap_nonce():
    with tempfile.TemporaryDirectory() as tmp:
        edge = Path(tmp) / "msedge.exe"
        edge.write_bytes(b"test executable placeholder")
        processes = []

        def launch(args):
            process = _FakeProcess(args)
            processes.append(process)
            return process

        shell = DesktopShell(
            data_dir=Path(tmp) / "data",
            demo=True,
            background=True,
            port=0,
            edge_path=edge,
            process_factory=launch,
        )
        shell.start()
        try:
            assert shell._activate_existing_window() is True
            first_url = shell.bootstrap_url
            assert first_url is not None
            assert _bootstrap_status(first_url) == 303
            first_session = parse_qs(urlsplit(first_url).query)["session"][0]

            processes[0].returncode = 0
            assert shell._activate_existing_window() is True
            second_url = shell.bootstrap_url
            assert second_url is not None
            second_session = parse_qs(urlsplit(second_url).query)["session"][0]
            assert second_session != first_session

            # The consumed first URL cannot be replayed, but the resident's
            # newly issued URL authenticates the replacement window.
            assert _bootstrap_status(first_url) == 404
            assert _bootstrap_status(second_url) == 303
        finally:
            shell.close()


def test_native_shell_starts_the_winui_client_with_named_pipe_credentials():
    with tempfile.TemporaryDirectory() as tmp:
        native = Path(tmp) / "Haven.Desktop.exe"
        native.write_bytes(b"test native executable placeholder")
        processes = []

        def launch(args, **kwargs):
            process = _FakeProcess(args, **kwargs)
            processes.append(process)
            return process

        shell = DesktopShell(
            data_dir=Path(tmp) / "data",
            native=True,
            native_path=native,
            port=0,
            process_factory=launch,
        )
        shell.start()
        try:
            assert shell.native_ipc is not None
            assert shell.native_ipc.is_running
            assert len(processes) == 1
            args = processes[0].args
            assert str(native) in args
            assert "--pipe-name" in args
            assert shell.native_ipc.pipe_name in args
            assert "--ipc-token-stdin" in args
            assert "--ipc-token" not in args
            assert shell.native_ipc.auth_token not in args
            assert "stdin" in processes[0].kwargs
            assert processes[0].stdin.data == (shell.native_ipc.auth_token + "\n").encode("utf-8")
            assert processes[0].stdin.closed is True
            assert shell.server_thread is None
            assert shell.server.socket.fileno() == -1
            assert not any(argument.startswith("--app=") for argument in args)
        finally:
            shell.close()


def test_second_background_launch_activates_the_existing_resident_window():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        edge = Path(tmp) / "msedge.exe"
        edge.write_bytes(b"test executable placeholder")
        processes = []

        def launch(args):
            process = _FakeProcess(args)
            processes.append(process)
            return process

        first = DesktopShell(
            data_dir=data_dir,
            demo=True,
            background=True,
            port=0,
            edge_path=edge,
            process_factory=launch,
        )
        second = DesktopShell(data_dir=data_dir, demo=True, background=True, port=0)
        first.start()
        try:
            with pytest.raises(DesktopShellAlreadyRunning, match="activation requested"):
                second.start()
            assert len(processes) == 1
            assert first.process is processes[0]
        finally:
            second.close()
            first.close()


def test_activation_record_does_not_store_plaintext_token_on_windows():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        shell = DesktopShell(data_dir=data_dir, demo=True, background=True, port=0)
        shell.start()
        try:
            path = data_dir / ".haven-desktop-control.json"
            raw = path.read_bytes()
            assert _read_activation_record(path) == (
                shell.server.server_address[1],
                shell.activation_token,
            )
            if os.name == "nt":
                assert shell.activation_token.encode("utf-8") not in raw
                assert b"token_protected" in raw
        finally:
            shell.close()


def test_background_shell_stays_resident_without_opening_edge():
    with tempfile.TemporaryDirectory() as tmp:
        shell = DesktopShell(
            data_dir=Path(tmp) / "data",
            demo=True,
            background=True,
            port=0,
            edge_path=Path(tmp) / "missing-edge.exe",
        )
        shell.start()
        try:
            assert shell.server is not None
            assert shell.server.server_address[1] != 0
            assert shell.process is None
            assert shell.bootstrap_url is None
        finally:
            shell.close()
        assert (Path(tmp) / "data" / ".haven-instance.lock").exists()


def test_data_dir_move_rebinds_the_desktop_instance_lock():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = Path(tmp) / "data"
        new_dir = Path(tmp) / "moved"
        shell = DesktopShell(data_dir=old_dir, demo=True, background=True, port=0)
        shell.start()
        try:
            assert (old_dir / ".haven-desktop-control.json").exists()
            result = shell.server.setup.choose_data_dir(str(new_dir))
            assert result["ok"] is True
            assert shell._instance_lock is not None
            assert shell._instance_lock.data_dir == new_dir.resolve()
            assert not (old_dir / ".haven-desktop-control.json").exists()
            assert (new_dir / ".haven-desktop-control.json").exists()

            old_probe = InstanceLock(old_dir).acquire()
            old_probe.release()
            with pytest.raises(InstanceAlreadyRunning):
                InstanceLock(new_dir).acquire()
        finally:
            shell.close()


def test_data_dir_move_reserves_target_before_moving_when_target_is_owned():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = Path(tmp) / "data"
        new_dir = Path(tmp) / "occupied"
        shell = DesktopShell(data_dir=old_dir, demo=True, background=True, port=0)
        shell.start()
        blocker = InstanceLock(new_dir).acquire()
        marker = old_dir / "history.db"
        marker.write_bytes(b"must stay put")
        try:
            result = shell.server.setup.choose_data_dir(str(new_dir))

            assert result["ok"] is False
            assert "could not reserve data dir" in result["error"]
            assert marker.read_bytes() == b"must stay put"
            assert not (new_dir / "history.db").exists()
            assert shell.server.setup_store.path.parent == old_dir.resolve()
            assert shell._instance_lock is not None
            assert shell._instance_lock.data_dir == old_dir.resolve()
            assert shell._reserved_instance_lock is None
        finally:
            blocker.release()
            shell.close()
