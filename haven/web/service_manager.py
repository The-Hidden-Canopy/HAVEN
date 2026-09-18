"""Logon startup management via the Windows Task Scheduler.

HAVEN is a per-user web app, so "start at logon" is honestest as a scheduled
task, not a Windows service: ``schtasks.exe`` ships with Windows, needs no
native wrapper and no new dependency (the stdlib-only rule holds), and can be
created and deleted by the running server through ``subprocess``. Every probe
goes through an injectable runner so tests never touch the real scheduler.

Security posture is deliberate: ``/RL LIMITED`` runs the task as the
installing user without elevation, so HAVEN stays a per-user service with
exactly the privileges of whoever installed it.

The task must relaunch the SAME installation it was installed from, so the
launch command pins the resolved data dir. When the installation lives at the
default (``~/.haven`` and no ``HAVEN_DATA_DIR`` override) the server's own
default matches and the command stays bare; otherwise the command sets
``HAVEN_DATA_DIR`` for the child process, since a scheduled task cannot carry
per-task environment variables.

Linux follow-up (not built here): the same ServiceManager shape backed by a
systemd user unit written under ``~/.config/systemd/user/`` would give the
equivalent ``install``/``uninstall``/``status`` on systemd desktops.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

TASK_NAME = "HAVEN"

_TIMEOUT_SECONDS = 15
_DATA_DIR_ENV = "HAVEN_DATA_DIR"
_DEFAULT_PORT = 8080

_UNAVAILABLE = "service management unavailable on this platform"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    # The timeout is a class-level contract: a hung schtasks must fail the
    # call, not wedge the UI thread that issued it.
    return subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)


def _tail(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


class ServiceManager:
    """Installs, removes, and probes the ``HAVEN`` logon task.

    ``port_getter`` exists because the server's bound port is only known
    after construction (port 0 hands out an ephemeral port at bind time):
    the launch command must be built lazily, at call time, so the task
    relaunches on the port the user is actually using. The runner seam
    keeps every schtasks call testable.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        port_getter: Callable[[], int] | None = None,
        runner=None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._port_getter = port_getter or (lambda: _DEFAULT_PORT)
        self._runner = runner or _run

    def status(self) -> dict:
        try:
            result = self._runner(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
        except Exception:
            # schtasks absent (non-Windows), hung past the timeout, or any
            # other runner failure: the UI gets a plain "unavailable", never
            # an exception.
            return {"installed": False, "running": False, "detail": _UNAVAILABLE}
        if result.returncode != 0:
            return {"installed": False, "running": False, "detail": _tail(result.stderr) or "not installed"}
        state = _parse_status(result.stdout)
        return {"installed": True, "running": state == "Running", "detail": state or "installed"}

    def install(self) -> dict:
        port = self._port_getter()
        try:
            result = self._runner(
                [
                    "schtasks",
                    "/Create",
                    "/F",
                    "/TN",
                    TASK_NAME,
                    "/SC",
                    "ONLOGON",
                    "/RL",
                    "LIMITED",
                    "/TR",
                    self._launch_command(port),
                ]
            )
        except Exception:
            return {"ok": False, "error": _UNAVAILABLE}
        if result.returncode != 0:
            return {"ok": False, "error": _tail(result.stderr) or "schtasks /Create failed"}
        return {"ok": True, "detail": f"HAVEN will start at logon on port {port}"}

    def uninstall(self) -> dict:
        try:
            result = self._runner(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        except Exception:
            return {"ok": False, "error": _UNAVAILABLE}
        if result.returncode != 0:
            return {"ok": False, "error": _tail(result.stderr) or "schtasks /Delete failed"}
        return {"ok": True, "detail": "removed"}

    def _launch_command(self, port: int) -> str:
        """The single `/TR` command string the logon task runs.

        Bare when the installation is at the server default; otherwise a
        `cmd /c` wrapper pins `HAVEN_DATA_DIR` for the child, because a
        scheduled task cannot carry its own environment.
        """

        base = f'"{sys.executable}" -m haven.web.server --port {port}'
        if _DATA_DIR_ENV not in os.environ and self._data_dir == Path.home() / ".haven":
            return base
        # `set VAR=value&&` runs the assignment into the command without a
        # trailing space becoming part of the value.
        return f'cmd /c "set {_DATA_DIR_ENV}={self._data_dir}&& {base}"'


def _parse_status(stdout: str | None) -> str | None:
    for line in (stdout or "").splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "status":
            value = value.strip()
            if value:
                return value
    return None


__all__ = ["TASK_NAME", "ServiceManager"]
