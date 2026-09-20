"""Desktop hosting is testable without launching a real browser window."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
    def __init__(self, args):
        self.args = args
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


def test_shell_starts_loopback_server_and_edge_app_mode_with_a_fresh_session():
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
