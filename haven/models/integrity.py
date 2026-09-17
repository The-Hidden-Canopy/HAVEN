"""Chunked sha256 verification for model files.

Endpoints skip integrity entirely: they have no local files, so there is
nothing to hash. Hash availability is first-class elsewhere (manifest
validation refuses a declared file without one); this module is the check
that makes those hashes mean something.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MB


def hash_file(path: str | Path) -> str:
    """Return the hex sha256 of a file, read in 1 MB chunks."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files(
    sha256_map: dict[str, str],
    folder: str | Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Check every listed file against its declared digest.

    Returns `(missing, mismatched)` as sorted tuples of relative paths;
    both empty means every file verified.
    """

    folder = Path(folder)
    missing: list[str] = []
    mismatched: list[str] = []
    for rel, expected in sha256_map.items():
        candidate = folder / rel
        if not candidate.is_file():
            missing.append(rel)
        elif hash_file(candidate) != expected:
            mismatched.append(rel)
    return tuple(sorted(missing)), tuple(sorted(mismatched))


__all__ = ["hash_file", "verify_files"]
