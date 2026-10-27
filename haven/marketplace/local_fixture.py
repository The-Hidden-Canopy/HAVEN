"""A free first-party content fixture, pinned to bytes shipped with HAVEN.

The published RFC 8032 public test key verifies this fixture ONLY. Its signed
envelope is also hash-pinned by the host; the key is never added to remote
marketplace trust. No secret key is shipped or generated at runtime.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from .lifecycle import LocalProductManager, LocalRelease, ProductError, _plain_path

_ROOT = Path(__file__).with_name("fixtures")
_CATALOG_SHA256 = "3f35353d4064dbbebc4aea463afa91a844d823b96c7d5c8965dbeae9e6e12ee6"
_PUBLIC_KEY = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
_PINS = (
    ("1.0.0", "939f378cb6a54393d694d1c2551b020c539a9c8f12e5d5e57447989f01b50e54", 305),
    ("1.1.0", "df8d7b11bc9317700226ecf3a74707c23831489412a67e71ee24e38810d4e45a", 317),
)


def _catalog() -> bytes:
    path = _ROOT / "catalog.json"
    _plain_path(path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _CATALOG_SHA256:
        raise ProductError("fixture_catalog_invalid", "bundled fixture catalog differs from the host pin")
    return raw


def create_local_fixture_manager(root: str | Path, *, authorize: Callable[[str, str], bool]) -> LocalProductManager:
    return LocalProductManager(
        root,
        catalog=_catalog,
        trusted_keys=lambda: {"rfc8032-local-fixture-only": _PUBLIC_KEY},
        releases=tuple(LocalRelease("haven-welcome", version, "any", digest, size, _ROOT / f"welcome-{version}.json") for version, digest, size in _PINS),
        platform="any",
        authorize=authorize,
    )


__all__ = ["create_local_fixture_manager"]
