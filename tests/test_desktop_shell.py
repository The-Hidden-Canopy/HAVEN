"""Desktop hosting is testable without launching a real browser window."""

from __future__ import annotations

import http.client
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from haven.desktop import shell as shell_module
from haven.desktop.shell import (
    DesktopShell,
    DesktopShellAlreadyRunning,
    _configure_logging,
    _normalize_process_exit_code,
    _read_activation_record,
    find_edge_executable,
    main,
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


def test_normalize_process_exit_code_folds_unsigned_crash_codes_to_signed():
    # A crashed native process reports its raw unsigned 32-bit NTSTATUS
    # (e.g. 0xC000027B); sys.exit() cannot carry that (it overflows a C
    # long), and the resulting OverflowError gets swallowed into a
    # meaningless generic code before it ever reaches the console. The
    # signed twin is representable and matches what other hosts (e.g.
    # .NET's Process.ExitCode) already report for the identical crash.
    assert _normalize_process_exit_code(0xC000027B) == -1073741189
    # An ordinary small exit code is untouched.
    assert _normalize_process_exit_code(0) == 0
    assert _normalize_process_exit_code(1) == 1
    # The boundary: the largest value that still fits a signed 32-bit int
    # unchanged, and the first value that must fold.
    assert _normalize_process_exit_code(0x7FFFFFFF) == 0x7FFFFFFF
    assert _normalize_process_exit_code(0x80000000) == -0x80000000


def test_native_window_crash_reports_a_representable_exit_code_and_explains_itself(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        native = Path(tmp) / "Haven.Desktop.exe"
        native.write_bytes(b"test native executable placeholder")

        class _CrashingProcess(_FakeProcess):
            def wait(self, timeout=None):
                # The raw unsigned form a real Windows crash reports.
                self.returncode = 0xC000027B
                return self.returncode

        def launch(args, **kwargs):
            return _CrashingProcess(args, **kwargs)

        shell = DesktopShell(
            data_dir=Path(tmp) / "data",
            native=True,
            native_path=native,
            port=0,
            process_factory=launch,
        )
        shell.start()
        exit_code = shell.wait()
        assert exit_code == -1073741189
        error = capsys.readouterr().err
        assert "0xC000027B" in error
        assert "-1073741189" in error


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


class _RecordingShell:
    """Stands in for DesktopShell to capture how ``main`` wires it up."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def run(self) -> int:
        return 0


def _isolate_data_dir(monkeypatch, tmp_path) -> None:
    """`main()` now configures logging (haven.log/haven-console.log) into
    the resolved data directory before constructing `DesktopShell` -- see
    `haven.desktop.shell._configure_logging`. Every test below calls
    `main()` with no `--data-dir`, so without this it would resolve to the
    real default (`~/.haven`, a real household's actual HAVEN installation
    directory on a dev machine) and write log files into it."""

    monkeypatch.setenv("HAVEN_DATA_DIR", str(tmp_path / "isolated-data"))


def test_main_launches_native_by_default(monkeypatch, tmp_path):
    _isolate_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(shell_module, "DesktopShell", _RecordingShell)

    assert main([]) == 0

    assert _RecordingShell.last_kwargs["native"] is True


def test_main_web_flag_falls_back_to_the_compatibility_host(monkeypatch, tmp_path):
    _isolate_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(shell_module, "DesktopShell", _RecordingShell)

    assert main(["--web"]) == 0
    assert _RecordingShell.last_kwargs["native"] is False

    assert main(["--debug-web"]) == 0
    assert _RecordingShell.last_kwargs["native"] is False


def test_main_web_flag_prints_a_debug_surface_banner(monkeypatch, tmp_path, capsys):
    _isolate_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(shell_module, "DesktopShell", _RecordingShell)

    main(["--web"])

    assert "debug" in capsys.readouterr().err.lower()


def test_main_native_default_prints_no_web_banner(monkeypatch, tmp_path, capsys):
    _isolate_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(shell_module, "DesktopShell", _RecordingShell)

    main([])

    assert capsys.readouterr().err == ""


# -- logging (run-haven.bat's detached pythonw/pyw launch) --------------------


@pytest.fixture()
def _release_log_handler_after():
    """`_configure_logging` deliberately keeps its own handler/stream open
    across calls (see its docstring) so it can replace them idempotently --
    a test must still release the one it opened, or Windows will refuse to
    delete `tmp_path` afterward because the file is still in use."""

    yield
    if shell_module._log_handler is not None:
        import logging

        logging.getLogger().removeHandler(shell_module._log_handler)
        shell_module._log_handler.close()
        shell_module._log_handler = None
    if shell_module._console_log_file is not None:
        shell_module._console_log_file.close()
        shell_module._console_log_file = None


def test_configure_logging_leaves_a_real_console_untouched(tmp_path, _release_log_handler_after):
    import sys

    original_stdout, original_stderr = sys.stdout, sys.stderr
    _configure_logging(tmp_path)

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
    assert (tmp_path / "haven.log").exists()


def test_configure_logging_redirects_a_missing_console_and_captures_a_crash(tmp_path, _release_log_handler_after):
    import sys

    original_stdout, original_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        _configure_logging(tmp_path)

        assert sys.stdout is not None and sys.stderr is not None  # no longer None: pythonw's real failure mode
        print("hello from a console-less process", file=sys.stderr)

        try:
            raise RuntimeError("simulated crash")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr

    assert "hello from a console-less process" in (tmp_path / "haven-console.log").read_text(encoding="utf-8")
    log_text = (tmp_path / "haven.log").read_text(encoding="utf-8")
    assert "unhandled exception" in log_text
    assert "RuntimeError: simulated crash" in log_text


def test_configure_logging_is_idempotent_across_repeated_calls(tmp_path, _release_log_handler_after):
    """`main()` runs `_configure_logging` once per process in production,
    but the test suite calls `main()` many times in one interpreter --
    this must not accumulate a growing stack of open file handlers pointed
    at the same (or a since-deleted) data directory."""

    import logging
    from logging.handlers import RotatingFileHandler

    _configure_logging(tmp_path)
    _configure_logging(tmp_path)
    _configure_logging(tmp_path)

    handlers_on_this_file = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == (tmp_path / "haven.log")
    ]
    assert len(handlers_on_this_file) == 1
