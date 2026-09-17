"""Chunked sha256: whole-file hashing and the (missing, mismatched) verify
contract used by discovery, download, and post-install checks.
"""

import hashlib
import tempfile
from pathlib import Path

from haven.models import hash_file, verify_files


def test_hash_file_matches_hashlib_digest():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "blob.bin"
        payload = b"x" * (3 * 1024 * 1024) + b"tail"  # spans chunk boundaries
        path.write_bytes(payload)
        assert hash_file(path) == hashlib.sha256(payload).hexdigest()


def test_verify_files_passes_when_everything_matches():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "a.bin").write_bytes(b"aaa")
        (folder / "b.bin").write_bytes(b"bbb")
        sha = {
            "a.bin": hashlib.sha256(b"aaa").hexdigest(),
            "b.bin": hashlib.sha256(b"bbb").hexdigest(),
        }
        assert verify_files(sha, folder) == ((), ())


def test_verify_files_reports_missing_and_mismatched_separately():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "a.bin").write_bytes(b"aaa")
        (folder / "b.bin").write_bytes(b"corrupted")
        sha = {
            "a.bin": hashlib.sha256(b"aaa").hexdigest(),
            "b.bin": hashlib.sha256(b"bbb").hexdigest(),
            "c.bin": hashlib.sha256(b"ccc").hexdigest(),
        }
        missing, mismatched = verify_files(sha, folder)
        assert missing == ("c.bin",)
        assert mismatched == ("b.bin",)
