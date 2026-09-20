"""A small cross-process lock for one HAVEN installation.

The lock is deliberately a held file descriptor, not a PID marker.  A PID
file can become stale after a crash and can be reused by another process;
the operating system releases this lock when the owning process exits.
"""

from __future__ import annotations

import os
from pathlib import Path


class InstanceLockError(RuntimeError):
    """The installation lock could not be opened or acquired."""


class InstanceAlreadyRunning(InstanceLockError):
    """Another HAVEN host currently owns this installation's lock."""


class InstanceLock:
    """Hold an OS-backed lock file for one data directory."""

    def __init__(self, data_dir: str | Path, *, filename: str = ".haven-instance.lock") -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / filename
        self._handle = None
        self._locked = False

    def acquire(self) -> "InstanceLock":
        if self._handle is not None:
            return self

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
        except OSError as exc:
            raise InstanceLockError(f"could not open HAVEN instance lock {self.path}: {exc}") from exc

        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            try:
                handle.close()
            except OSError:
                pass
            # The lock operation is the only operation in this block that
            # maps to "already running".  Open/create failures above remain
            # actionable installation errors.
            raise InstanceAlreadyRunning(
                f"HAVEN is already running for data directory {self.data_dir}"
            ) from exc

        self._handle = handle
        self._locked = True
        return self

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if self._locked:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Closing the descriptor still releases an OS lock.  Shutdown
            # must not turn a successful app exit into a secondary failure.
            pass
        finally:
            self._locked = False
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self) -> "InstanceLock":
        return self.acquire()

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()


__all__ = ["InstanceAlreadyRunning", "InstanceLock", "InstanceLockError"]
