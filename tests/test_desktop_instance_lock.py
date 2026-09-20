"""The desktop host owns one process slot per HAVEN data directory."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from haven.desktop.instance_lock import InstanceAlreadyRunning, InstanceLock


def test_lock_rejects_a_second_owner_and_can_be_reacquired_after_release():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        first = InstanceLock(data_dir).acquire()
        second = InstanceLock(data_dir)
        try:
            with pytest.raises(InstanceAlreadyRunning):
                second.acquire()
        finally:
            first.release()

        second.acquire()
        second.release()
