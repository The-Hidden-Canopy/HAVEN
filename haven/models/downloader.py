"""Inspect-before-install URL flow, stdlib urllib only.

`inspect_manifest_url` fetches and parses a manifest and nothing else: the
weights are never downloaded at inspect time, so a hostile or broken URL
cannot make HAVEN write a byte of model data. `download_files` streams the
declared files and hash-verifies them afterwards; a mismatch raises
naming the files and removes the partial destination. HAVEN executes no
remote code (`remote_code` is always "none") and treats a manifest that
declares hashes as the contract for what lands on disk.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .contracts import ModelKind
from .integrity import hash_file
from .manifest import ManifestError, ModelManifest

_TIMEOUT_SECONDS = 15
_MAX_MANIFEST_BYTES = 5 * 1024 * 1024


class ModelSourceError(RuntimeError):
    """Raised when a manifest URL cannot be fetched or parsed."""


class HashMismatchError(RuntimeError):
    """Raised when downloaded files fail sha256 verification."""

    def __init__(self, mismatched: tuple[str, ...], missing: tuple[str, ...] = ()) -> None:
        self.mismatched = tuple(mismatched)
        self.missing = tuple(missing)
        parts = []
        if mismatched:
            parts.append("sha256 mismatch: " + ", ".join(mismatched))
        if missing:
            parts.append("missing after download: " + ", ".join(missing))
        super().__init__("; ".join(parts) or "hash verification failed")


@dataclass(frozen=True)
class UrlInspection:
    manifest: ModelManifest
    file_count: int
    declared_bytes: int | None
    license: str | None
    backend: str
    languages: frozenset[str]
    hardware: frozenset[str]
    hash_verification: bool
    remote_code: str = "none"


def _base_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def _fetch(url: str, *, limit: int | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "haven-model-manager"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read(limit) if limit is not None else response.read()
    except urllib.error.URLError as exc:
        raise ModelSourceError(f"cannot fetch {url}: {exc}") from exc


def inspect_manifest_url(url: str) -> UrlInspection:
    """Fetch and parse a manifest URL only; never downloads weights."""

    body = _fetch(url, limit=_MAX_MANIFEST_BYTES)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelSourceError(f"manifest at {url} is not valid JSON: {exc}") from exc
    try:
        manifest = ModelManifest.from_dict(data)
    except ManifestError as exc:
        raise ModelSourceError(f"manifest at {url} is invalid: {exc}") from exc
    return UrlInspection(
        manifest=manifest,
        file_count=len(manifest.files),
        declared_bytes=None,
        license=manifest.license,
        backend=manifest.backend,
        languages=manifest.languages,
        hardware=manifest.device_support,
        hash_verification=bool(manifest.sha256),
        remote_code="none",
    )


def download_files(
    manifest: ModelManifest,
    base_url: str | None = None,
    dest_dir: str | Path | None = None,
    progress=None,
) -> Path:
    """Download every declared file (flat by basename) and hash-verify.

    `base_url` defaults to the manifest URL's directory; on any hash
    mismatch the partial destination is removed.
    """

    if base_url is None:
        if not manifest.source:
            raise ModelSourceError("base_url is required when the manifest declares no source URL")
        base_url = _base_url(manifest.source)
    dest = Path(dest_dir) if dest_dir is not None else Path(manifest.id)
    created = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for role, rel in manifest.files.items():
            url = urllib.parse.urljoin(base_url, Path(rel).name)
            request = urllib.request.Request(url, headers={"User-Agent": "haven-model-manager"})
            target = dest / Path(rel).name
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                    with open(target, "wb") as handle:
                        while chunk := response.read(64 * 1024):
                            handle.write(chunk)
                            if progress is not None:
                                progress(len(chunk), Path(rel).name)
            except urllib.error.URLError as exc:
                raise ModelSourceError(f"cannot download {url}: {exc}") from exc
            written.append(target)
        missing = [rel for rel in manifest.files.values() if rel not in manifest.sha256]
        missing += [rel for rel, target in ((rel, dest / Path(rel).name) for rel in manifest.files.values()) if not target.is_file()]
        mismatched = [
            rel
            for rel in manifest.files.values()
            if (dest / Path(rel).name).is_file() and hash_file(dest / Path(rel).name) != manifest.sha256.get(rel, "")
        ]
        if missing or mismatched:
            raise HashMismatchError(tuple(sorted(set(mismatched))), tuple(sorted(set(missing))))
    except BaseException:
        for target in written:
            target.unlink(missing_ok=True)
        if created:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


__all__ = [
    "HashMismatchError",
    "ModelSourceError",
    "UrlInspection",
    "download_files",
    "inspect_manifest_url",
]
