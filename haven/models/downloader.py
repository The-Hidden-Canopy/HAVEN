"""Inspect-before-install URL flow, stdlib urllib only.

`inspect_manifest_url` resolves a URL to a manifest and parses it, nothing
else: the weights are never downloaded at inspect time, so a hostile or
broken URL cannot make HAVEN write a byte of model data. Resolution accepts
three shapes: a URL that is (or serves) a `haven-model.json`, a Hugging Face
repo URL (`huggingface.co/<org>/<repo>`, `/models/...`, `/tree/...`, `hf.co`)
whose manifest is fetched from `/raw/main/` and -- when absent -- synthesized
from the repo's file listing via the same layout heuristics as folder
detection, and anything else is a typed `ModelSourceError` naming the
accepted shapes. `download_files` streams the declared files and
hash-verifies them afterwards; a mismatch raises naming the files and
removes the partial destination. HAVEN executes no remote code
(`remote_code` is always "none") and treats a manifest that declares hashes
as the contract for what lands on disk. Files stream to `.part` siblings
and are renamed into place only when complete; a leftover `.part` is
resumed with a Range request when the server supports it, a cooperative
`cancel` callable aborts between chunks, and the progress callback reports
cumulative byte counts against the server's declared totals.
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
from .detect import CheckpointTooLargeError, synthesize_from_files
from .integrity import hash_file
from .manifest import ManifestError, ModelManifest, manifest_filename

_TIMEOUT_SECONDS = 15
_MAX_MANIFEST_BYTES = 5 * 1024 * 1024

_HF_HOSTS = ("huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co")


class ModelSourceError(RuntimeError):
    """Raised when a model URL cannot be resolved, fetched, or parsed."""


class DownloadCancelledError(ModelSourceError):
    """Raised when a download is abandoned via its cancel callable."""


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
    resolved_from: str | None = None


@dataclass(frozen=True)
class ResolvedSource:
    """A URL resolved to a manifest plus the base its files download from."""

    manifest: ModelManifest
    base_url: str
    manifest_url: str | None = None
    via: str = "manifest"
    resolved_from: str | None = None


def _base_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def _fetch(url: str, *, limit: int | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "haven-model-manager"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read(limit) if limit is not None else response.read()
    except urllib.error.URLError as exc:
        raise ModelSourceError(f"cannot fetch {url}: {exc}") from exc


def _parse_manifest(body: bytes, url: str) -> ModelManifest:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelSourceError(f"manifest at {url} is not valid JSON: {exc}") from exc
    try:
        return ModelManifest.from_dict(data)
    except ManifestError as exc:
        raise ModelSourceError(f"manifest at {url} is invalid: {exc}") from exc


def _fetch_manifest(url: str) -> ModelManifest:
    return _parse_manifest(_fetch(url, limit=_MAX_MANIFEST_BYTES), url)


def _is_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _parse_hf_repo(parsed: urllib.parse.SplitResult) -> tuple[str, str, str] | None:
    """Map an HF-shaped path to (host, org, repo); any host, for testability.

    Accepted shapes: `/{org}/{repo}`, `/models/{org}/{repo}` (the website
    hub route; the git path drops the `models` prefix), and
    `/{org}/{repo}/tree/{revision}[/...]`.
    """

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2:
        org, repo = parts
    elif len(parts) == 3 and parts[0] == "models":
        org, repo = parts[1], parts[2]
    elif len(parts) >= 4 and parts[2] == "tree":
        org, repo = parts[0], parts[1]
    else:
        return None
    if not org or not repo:
        return None
    return parsed.netloc.lower(), org, repo


def _hf_api_url(scheme: str, host: str, org: str, repo: str) -> str:
    api_host = "huggingface.co" if host in _HF_HOSTS else host
    return f"{scheme}://{api_host}/api/models/{org}/{repo}"


def _fetch_hf_file_listing(url: str) -> list[str]:
    body = _fetch(url, limit=_MAX_MANIFEST_BYTES)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelSourceError(f"Hugging Face API response at {url} is not valid JSON: {exc}") from exc
    siblings = data.get("siblings") if isinstance(data, dict) else None
    if not isinstance(siblings, list):
        raise ModelSourceError(f"Hugging Face API response at {url} lists no files")
    filenames = []
    for sibling in siblings:
        if isinstance(sibling, dict) and isinstance(sibling.get("rfilename"), str):
            filenames.append(sibling["rfilename"])
    if not filenames:
        raise ModelSourceError(f"Hugging Face API response at {url} lists no files")
    return filenames


def resolve_model_url(url: str) -> ResolvedSource:
    """Resolve a user-supplied URL to a manifest and a download base URL.

    (a) A URL that is or serves `haven-model.json` -> that manifest, files
        addressed relative to the manifest's directory.
    (b) Hugging Face repo shapes -> the manifest at `/raw/main/` when
        present; otherwise a manifest synthesized from the repo file listing,
        files addressed by basename under `/resolve/main/`.
    (c) Anything else -> `ModelSourceError` naming the accepted shapes.
    """

    if not isinstance(url, str) or not _is_http(url.strip()):
        raise ModelSourceError(
            f"cannot resolve {url!r}: expected an http(s) haven-model.json URL "
            "or a Hugging Face repo URL like https://huggingface.co/<org>/<repo>"
        )
    url = url.strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.path.rstrip("/").endswith(manifest_filename()):
        return ResolvedSource(
            manifest=_fetch_manifest(url),
            base_url=_base_url(url),
            manifest_url=url,
        )
    repo = _parse_hf_repo(parsed)
    if repo is not None:
        host, org, repo_name = repo
        base = f"{parsed.scheme}://{host}/{org}/{repo_name}/resolve/main/"
        raw_url = f"{parsed.scheme}://{host}/{org}/{repo_name}/raw/main/{manifest_filename()}"
        try:
            manifest = _fetch_manifest(raw_url)
        except ModelSourceError as exc:
            missing = isinstance(exc.__cause__, urllib.error.HTTPError) and exc.__cause__.code == 404
            if not missing:
                raise
            manifest = None
        if manifest is not None:
            return ResolvedSource(
                manifest=manifest,
                base_url=base,
                manifest_url=raw_url,
                via="huggingface",
                resolved_from=url,
            )
        filenames = _fetch_hf_file_listing(
            _hf_api_url(parsed.scheme, host, org, repo_name)
        )
        try:
            manifest = synthesize_from_files(
                filenames,
                name_hint=repo_name,
                source=f"huggingface:{org}/{repo_name}",
            )
        except CheckpointTooLargeError as exc:
            raise ModelSourceError(f"repo {org}/{repo_name}: {exc}") from exc
        if manifest is None:
            raise ModelSourceError(
                f"repo {org}/{repo_name} matches no known layout "
                "(looked for a single *.gguf, config.json with *.safetensors/*.bin, or *.onnx)"
            )
        return ResolvedSource(
            manifest=manifest,
            base_url=base,
            via="huggingface",
            resolved_from=url,
        )
    try:
        manifest = _fetch_manifest(url)
    except ModelSourceError as exc:
        raise ModelSourceError(
            f"cannot resolve {url!r} as a model source: {exc}; accepted shapes are "
            "a haven-model.json URL or a Hugging Face repo URL "
            "(https://huggingface.co/<org>/<repo>)"
        ) from exc
    return ResolvedSource(
        manifest=manifest,
        base_url=_base_url(url),
        manifest_url=url,
    )


def _inspection_from_manifest(
    manifest: ModelManifest,
    *,
    resolved_from: str | None = None,
) -> UrlInspection:
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
        resolved_from=resolved_from,
    )


def inspect_manifest_url(url: str) -> UrlInspection:
    """Resolve and parse a model URL only; never downloads weights."""

    resolved = resolve_model_url(url)
    return _inspection_from_manifest(
        resolved.manifest,
        resolved_from=resolved.resolved_from,
    )


def _content_length_total(headers) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _content_range_total(headers, resumed_from: int, headers_content_length: int | None) -> int | None:
    """Total file size from a `Content-Range: bytes N-M/TOTAL` header."""

    raw = headers.get("Content-Range")
    if raw and "/" in raw:
        total = raw.rsplit("/", 1)[1].strip()
        if total.isdigit():
            return int(total)
    if headers_content_length is not None:
        return resumed_from + headers_content_length
    return None


def download_files(
    manifest: ModelManifest,
    base_url: str | None = None,
    dest_dir: str | Path | None = None,
    progress=None,
    *,
    cancel=None,
) -> Path:
    """Download every declared file (flat by basename) and hash-verify.

    `base_url` defaults to the manifest URL's directory; on any hash
    mismatch the partial destination is removed. Every file streams to
    `<name>.part` and is renamed to its final name only on success, so an
    interrupted file can be resumed: a pre-existing `.part` is offered a
    `Range: bytes=<existing>-` request and appended to only when the server
    answers 206 (a 200 restarts the file from scratch). `progress` is called
    as `progress(received_bytes, total_bytes, file_name)` -- once with
    `(0, total, name)` at each file start, once per chunk, and once with
    `(size, size, name)` on completion; `total_bytes` comes from
    Content-Length/Content-Range and is None when the server did not say.
    `cancel`, when given, is a no-arg callable checked between chunks; it
    returning True raises `DownloadCancelledError`.
    """

    if base_url is None:
        if not manifest.source:
            raise ModelSourceError("base_url is required when the manifest declares no source URL")
        base_url = _base_url(manifest.source)
    dest = Path(dest_dir) if dest_dir is not None else Path(manifest.id)
    created = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []  # final names this call already renamed
    partials: list[Path] = []  # .part files this call created or truncated
    try:
        for role, rel in manifest.files.items():
            if cancel is not None and cancel():
                raise DownloadCancelledError(f"download cancelled before {Path(rel).name}")
            name = Path(rel).name
            url = urllib.parse.urljoin(base_url, name)
            target = dest / name
            part = dest / f"{name}.part"
            resumed_from = part.stat().st_size if part.is_file() else 0
            request = urllib.request.Request(url, headers={"User-Agent": "haven-model-manager"})
            if resumed_from:
                request.add_header("Range", f"bytes={resumed_from}-")
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                    status = getattr(response, "status", 200)
                    if resumed_from and status == 206:
                        received = resumed_from
                        length = _content_length_total(response.headers)
                        total = _content_range_total(response.headers, resumed_from, length)
                        mode = "ab"
                    else:
                        resumed_from = 0
                        received = 0
                        total = _content_length_total(response.headers)
                        mode = "wb"
                        partials.append(part)
                    if progress is not None:
                        progress(received, total, name)
                    with open(part, mode) as handle:
                        while chunk := response.read(64 * 1024):
                            handle.write(chunk)
                            received += len(chunk)
                            if progress is not None:
                                progress(received, total, name)
                            if cancel is not None and cancel():
                                raise DownloadCancelledError(f"download cancelled: {name}")
            except urllib.error.URLError as exc:
                raise ModelSourceError(f"cannot download {url}: {exc}") from exc
            part.replace(target)
            installed.append(target)
            if progress is not None:
                progress(target.stat().st_size, target.stat().st_size, name)
        # Hash verification only applies when the manifest declares hashes; a
        # synthesized (hashless) manifest downloads unverified by definition.
        missing: list[str] = []
        mismatched: list[str] = []
        if manifest.sha256:
            missing = [rel for rel in manifest.files.values() if rel not in manifest.sha256]
            missing += [
                rel
                for rel in manifest.files.values()
                if rel in manifest.sha256 and not (dest / Path(rel).name).is_file()
            ]
            mismatched = [
                rel
                for rel in manifest.files.values()
                if rel in manifest.sha256
                and (dest / Path(rel).name).is_file()
                and hash_file(dest / Path(rel).name) != manifest.sha256[rel]
            ]
        if missing or mismatched:
            raise HashMismatchError(tuple(sorted(set(mismatched))), tuple(sorted(set(missing))))
    except BaseException:
        for part in partials:
            part.unlink(missing_ok=True)
        for target in installed:
            target.unlink(missing_ok=True)
        if created:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


__all__ = [
    "DownloadCancelledError",
    "HashMismatchError",
    "ModelSourceError",
    "ResolvedSource",
    "UrlInspection",
    "download_files",
    "inspect_manifest_url",
    "resolve_model_url",
]
