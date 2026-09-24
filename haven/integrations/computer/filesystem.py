"""`FilesystemProvider`: the first real "computer" resource provider.

Satisfies `haven.perception.observation.ObservationProvider` for reading
(one `haven.resources.ResourceRecord` per file/folder under an explicitly
allowed root), keeps `haven.execution.registry.ExecutionAdapter` for legacy
device-shaped compatibility, and provides the provider-neutral
`.execute_provider(ProviderCommand) -> ProviderResult` seam for computer
capabilities. It plugs into the same observation/execution boundaries as a
Home Assistant or community provider -- nothing here is a special case Haven
Core needs to know about.

The one non-negotiable safety property, matching the "canonical path
checks, allowed roots" discipline real filesystem tools need: every path
this provider touches, on both the read and write side, is resolved
(symlinks and `..` followed) and checked against the household's own
explicitly declared `allowed_roots` *at the moment of use*, never trusted
from a caller's string. `AuthorityEngine` deciding a command is allowed is
necessary but not sufficient here -- this provider re-validates unconditionally,
the same defense-in-depth discipline `haven.integrations.bluetooth` and
every other real integration in this repo already applies rather than
trusting a single upstream check.

Mutations are deliberately conservative: none of them overwrite an existing
destination silently, and each one re-observes its own result before
reporting success (a `move` isn't done until the destination exists AND the
source is gone) -- "the OS call didn't raise" is not the same claim as
"reality is now what was asked for," and this provider never conflates them.

No auto-discovery of allowed roots: a household names them explicitly (the
setup step this provider does not implement itself), matching the
`enroll_device()` discipline that a device becoming controllable requires a
deliberate human decision, never an inferred default.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, overload

from haven.core.domain import DeviceCommand, DeviceResult, RiskTier
from haven.execution import ProviderCommand, ProviderResult
from haven.resources.models import ResourceRecord

PROVIDER_ID = "local_filesystem"

_HASH_CHUNK_BYTES = 1 << 20
_DEFAULT_MAX_ENTRIES = 5000

# `haven.actions.ResourceAuthorityEngine`'s risk table for this provider's
# actions. `create_folder`/`copy` are purely additive -- they never touch an
# existing thing, and undoing one is trivial -- so they run without asking
# twice, the same "safe automatic" tier a light or thermostat gets. `move`/
# `rename` relocate or rename the household's only copy of something real;
# the provider's own no-overwrite guarantee keeps that from ever destroying
# data, but "the file is still there, just not where you expect it" is
# exactly the kind of surprise a household should confirm once before it
# happens, matching the two-step confirmation a garage door or door lock
# already gets.
FILESYSTEM_ACTION_RISK = {
    "filesystem.open": RiskTier.SAFE_AUTOMATIC,
    "filesystem.reveal": RiskTier.SAFE_AUTOMATIC,
    "filesystem.create_folder": RiskTier.SAFE_AUTOMATIC,
    "filesystem.copy": RiskTier.SAFE_AUTOMATIC,
    "filesystem.move": RiskTier.CONFIRMATION_REQUIRED,
    "filesystem.rename": RiskTier.CONFIRMATION_REQUIRED,
}

# `os.startfile()` follows the Windows file association.  That is useful for
# documents and media, but it also launches executables and scripts.  Keep the
# first computer surface deliberately view-oriented: unknown file types get
# `filesystem.reveal`, not an implicit process launch.  Direct provider calls
# enforce the same boundary as observed capabilities do.
_OPENABLE_SUFFIXES = frozenset(
    {
        ".avi",
        ".bmp",
        ".csv",
        ".doc",
        ".docx",
        ".flac",
        ".gif",
        ".htm",
        ".html",
        ".jpeg",
        ".jpg",
        ".json",
        ".markdown",
        ".md",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".odt",
        ".pdf",
        ".png",
        ".rst",
        ".rtf",
        ".svg",
        ".tsv",
        ".txt",
        ".wav",
        ".webp",
        ".xml",
        ".yaml",
        ".yml",
    }
)

_FILESYSTEM_MUTATIONS = frozenset(
    {
        "filesystem.create_folder",
        "filesystem.copy",
        "filesystem.move",
        "filesystem.rename",
    }
)


class PathOutsideAllowedRoots(ValueError):
    """Raised whenever a path this provider is asked to touch resolves
    outside every allowed root -- never silently ignored or clamped."""


def _canonical(path: str | Path) -> Path:
    return Path(path).resolve()


def _within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resource_id_for(path: Path) -> str:
    return f"file:{path.as_posix()}"


def _native_open(path: Path) -> None:
    """Ask the local operating system to open one already-confined path."""

    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OSError("native file open is only available on Windows")
    startfile(str(path))


def _native_reveal(path: Path) -> None:
    """Reveal one already-confined path in Windows Explorer."""

    if os.name != "nt":
        raise OSError("native file reveal is only available on Windows")
    target = str(path) if path.is_dir() else f"/select,{path}"
    # No shell=True: the path is data, not a command fragment.  The process
    # is intentionally asynchronous; ProviderResult means Explorer accepted
    # the request, not that a GUI window has finished painting.
    subprocess.Popen(["explorer.exe", target], close_fds=True)


@dataclass(frozen=True)
class FilesystemProviderConfig:
    """What a household explicitly declared, never inferred."""

    allowed_roots: tuple[Path, ...]
    scope_id: str
    read_only: bool = True
    max_entries: int = _DEFAULT_MAX_ENTRIES


class FilesystemProvider:
    """Observes and (optionally) mutates files under explicitly allowed roots."""

    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        allowed_roots: Iterable[str | Path],
        scope_id: str,
        read_only: bool = True,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Callable[[], datetime] | None = None,
        open_path: Callable[[Path], None] | None = None,
        reveal_path: Callable[[Path], None] | None = None,
    ) -> None:
        roots = tuple(_canonical(root) for root in allowed_roots)
        if not roots:
            raise ValueError("at least one allowed root is required")
        for root in roots:
            if not root.is_dir():
                raise ValueError(f"allowed root does not exist or is not a directory: {root}")
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError("scope_id must be a non-empty string")
        self._config = FilesystemProviderConfig(
            allowed_roots=roots, scope_id=scope_id.strip(), read_only=read_only, max_entries=max_entries
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._open_path = open_path or _native_open
        self._reveal_path = reveal_path or _native_reveal

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return self._config.allowed_roots

    # -- safety ---------------------------------------------------------

    def _require_within_roots(self, path: str | Path) -> Path:
        resolved = _canonical(path)
        if not _within_any_root(resolved, self._config.allowed_roots):
            raise PathOutsideAllowedRoots(f"{resolved} is outside every allowed root")
        return resolved

    # -- observation ------------------------------------------------------

    def observe(self) -> tuple[ResourceRecord, ...]:
        """One `ResourceRecord` per file/folder under an allowed root.

        Capped at `max_entries` as a safety/performance bound, not a
        completeness guarantee -- a household with a very large tree gets a
        partial, honest snapshot rather than this call hanging.
        """

        now = self._clock()
        records: list[ResourceRecord] = []
        for root in self._config.allowed_roots:
            for entry in self._walk(root):
                if len(records) >= self._config.max_entries:
                    return tuple(records)
                record = self._record_for(entry, now=now)
                if record is not None:
                    records.append(record)
        return tuple(records)

    def resource_id_for(self, path: str | Path) -> str:
        """The `ResourceRecord.resource_id` this provider would use for
        `path` once canonicalized -- the seam a caller uses to mark a
        moved-away source stale without observing it again (it can't: the
        path is gone by the time a move or rename has already succeeded).
        """

        return _resource_id_for(_canonical(path))

    def observe_one(self, path: str | Path) -> ResourceRecord | None:
        """Re-observe exactly one path -- the seam a caller uses to verify a
        mutation's consequence (did the resource store's own record of this
        path become true again) without paying for a full `observe()`
        rescan of every allowed root. Returns `None` for a path outside
        every allowed root or one that no longer exists, exactly as
        `observe()`'s own per-entry skip already does.
        """

        return self._record_for(Path(path), now=self._clock())

    def read_text(self, resource: ResourceRecord) -> str | None:
        """Read one text resource after re-checking the provider boundary.

        Knowledge extraction is deliberately downstream from observation,
        but it must not trust an old locator: a symlink or permission can
        change between the scan and extraction. Reusing this provider-owned
        read seam keeps the same canonical-root rule on both operations.
        """

        if resource.locator is None:
            return None
        try:
            resolved = self._require_within_roots(resource.locator)
        except PathOutsideAllowedRoots:
            # The locator may have become unsafe after observation (for
            # example, a symlink target changed).  Extraction should treat
            # that as unavailable content, not fail the whole scan.
            return None
        if not resolved.is_file():
            return None
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def read_bytes(self, resource: ResourceRecord) -> bytes | None:
        """Binary counterpart of `read_text` for format adapters (DOCX/PDF).

        Same canonical-root re-check at read time; the knowledge pipeline
        never opens the locator itself.
        """

        if resource.locator is None:
            return None
        try:
            resolved = self._require_within_roots(resource.locator)
        except PathOutsideAllowedRoots:
            return None
        if not resolved.is_file():
            return None
        try:
            return resolved.read_bytes()
        except OSError:
            return None

    def _walk(self, root: Path):
        yield root
        try:
            children = sorted(root.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir() and not child.is_symlink():
                yield from self._walk(child)
            else:
                yield child

    def _record_for(self, path: Path, *, now: datetime) -> ResourceRecord | None:
        # `path` may itself be a symlink (a file symlink -- `_walk` already
        # refuses to descend into a symlinked *directory*, but still yields
        # a symlinked *file* entry unresolved). Every stat/hash/read below
        # follows symlinks, so the boundary this provider exists to enforce
        # has to be checked against where the link actually points, not
        # where it appears to live -- resolving here is what
        # `_require_within_roots` already does, so a symlink whose target
        # resolves outside every allowed root is skipped before anything
        # touches it, the same way an out-of-bounds mutation target already
        # is on the write side.
        try:
            resolved = self._require_within_roots(path)
        except PathOutsideAllowedRoots:
            return None
        try:
            is_dir = resolved.is_dir()
            stat = resolved.stat()
        except OSError:
            return None
        capabilities = ["filesystem.read", "filesystem.reveal"]
        if self._can_open(resolved, is_dir=is_dir):
            capabilities.insert(1, "filesystem.open")
        if not self._config.read_only:
            capabilities += ["filesystem.move", "filesystem.rename"]
            if is_dir:
                capabilities.append("filesystem.create_folder")
            else:
                capabilities.append("filesystem.copy")
        content_hash = None
        if not is_dir:
            try:
                content_hash = _sha256_of(resolved)
            except OSError:
                content_hash = None
        return ResourceRecord(
            resource_id=_resource_id_for(resolved),
            resource_type="folder" if is_dir else "file",
            scope_id=self._config.scope_id,
            provider_id=self.provider_id,
            title=resolved.name or str(resolved),
            locator=str(resolved),
            capabilities=tuple(capabilities),
            observed_at=now,
            content_hash=content_hash,
            metadata=(("size_bytes", stat.st_size), ("modified_at", stat.st_mtime)),
        )

    # -- execution --------------------------------------------------------

    @overload
    def execute(self, command: DeviceCommand) -> DeviceResult:
        ...

    @overload
    def execute(self, command: ProviderCommand) -> ProviderResult:
        ...

    def execute(self, command: DeviceCommand | ProviderCommand) -> DeviceResult | ProviderResult:
        """Compatibility entry point for the original device-shaped seam."""

        if isinstance(command, ProviderCommand):
            return self.execute_provider(command)
        if command.service in {"filesystem.open", "filesystem.reveal"}:
            result = self.execute_provider(
                ProviderCommand(
                    request_id=command.request_id,
                    provider_id=self.provider_id,
                    capability=command.service,
                    target_resource_id=command.target_device_id,
                    parameters=command.parameters,
                    requested_at=command.requested_at,
                )
            )
            return DeviceResult(
                success=result.success,
                detail=result.detail,
                observed_at=result.observed_at,
                source=result.source,
            )
        return self._execute_device(command)

    def execute_provider(self, command: ProviderCommand) -> ProviderResult:
        """Execute an open-vocabulary, already-authorized provider command.

        Computer actions use this method so the resource path no longer has
        to masquerade as a device command.  Mutations deliberately delegate
        to the legacy implementation below, preserving its tested behavior
        while the new open/reveal capabilities establish the generic seam.
        """

        now = self._clock()
        if command.provider_id != self.provider_id:
            return ProviderResult(
                success=False,
                detail=f"command targets provider {command.provider_id!r}, not {self.provider_id!r}",
                observed_at=now,
                source=self.provider_id,
            )
        params = dict(command.parameters)
        try:
            if command.capability == "filesystem.open":
                return self._open(params, now=now)
            if command.capability == "filesystem.reveal":
                return self._reveal(params, now=now)
            if command.capability not in _FILESYSTEM_MUTATIONS:
                return ProviderResult(
                    success=False,
                    detail=f"unknown filesystem capability: {command.capability!r}",
                    observed_at=now,
                    source=self.provider_id,
                )
            result = self._execute_device(
                DeviceCommand(
                    request_id=command.request_id,
                    target_device_id=command.target_resource_id or command.capability,
                    service=command.capability,
                    parameters=command.parameters,
                    requested_at=command.requested_at,
                )
            )
            return ProviderResult(
                success=result.success,
                detail=result.detail,
                observed_at=result.observed_at,
                source=result.source,
            )
        except PathOutsideAllowedRoots as exc:
            return ProviderResult(success=False, detail=str(exc), observed_at=now, source=self.provider_id)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            detail = str(exc) or exc.__class__.__name__
            return ProviderResult(
                success=False,
                detail=f"{command.capability} failed: {detail}",
                observed_at=now,
                source=self.provider_id,
            )

    def _existing_target(self, params: dict) -> Path:
        target = self._require_within_roots(params["path"])
        if not target.exists():
            raise FileNotFoundError(str(target))
        return target

    @staticmethod
    def _can_open(target: Path, *, is_dir: bool | None = None) -> bool:
        """Whether `filesystem.open` is a view/open operation, not launch."""

        directory = target.is_dir() if is_dir is None else is_dir
        if directory:
            return True
        return target.suffix.lower() in _OPENABLE_SUFFIXES

    def _open(self, params: dict, *, now: datetime) -> ProviderResult:
        target = self._existing_target(params)
        if not self._can_open(target):
            return ProviderResult(
                success=False,
                detail=(
                    f"filesystem.open is not available for {target.name!r}; "
                    "use filesystem.reveal for this file"
                ),
                observed_at=now,
                source=self.provider_id,
            )
        try:
            self._open_path(target)
        except OSError as exc:
            detail = str(exc) or exc.__class__.__name__
            return ProviderResult(
                success=False,
                detail=f"could not open {target}: {detail}",
                observed_at=now,
                source=self.provider_id,
            )
        return ProviderResult(
            success=True,
            detail=f"requested open {target}",
            observed_at=self._clock(),
            source=self.provider_id,
            metadata=(("operation", "open"), ("path", str(target))),
        )

    def _reveal(self, params: dict, *, now: datetime) -> ProviderResult:
        target = self._existing_target(params)
        try:
            self._reveal_path(target)
        except OSError as exc:
            detail = str(exc) or exc.__class__.__name__
            return ProviderResult(
                success=False,
                detail=f"could not reveal {target}: {detail}",
                observed_at=now,
                source=self.provider_id,
            )
        return ProviderResult(
            success=True,
            detail=f"requested reveal {target}",
            observed_at=self._clock(),
            source=self.provider_id,
            metadata=(("operation", "reveal"), ("path", str(target))),
        )

    def _execute_device(self, command: DeviceCommand) -> DeviceResult:
        """Route an already-authorized command to a mutation method.

        `command.target_device_id` names the resource id the command acts
        on (its source, for a move/copy/rename); `command.parameters`
        carries whatever else that operation needs. An unknown service, or
        any mutation attempted while this provider was configured
        `read_only`, fails as an ordinary `DeviceResult` -- never a raised
        exception a caller has to specially handle.
        """

        now = self._clock()
        if self._config.read_only:
            return DeviceResult(
                success=False, detail="this filesystem provider is configured read-only", observed_at=now,
                source=self.provider_id,
            )
        params = dict(command.parameters)
        try:
            if command.service == "filesystem.create_folder":
                return self._create_folder(params, now=now)
            if command.service == "filesystem.copy":
                return self._copy(params, now=now)
            if command.service == "filesystem.move":
                return self._move(params, now=now)
            if command.service == "filesystem.rename":
                return self._rename(params, now=now)
        except PathOutsideAllowedRoots as exc:
            return DeviceResult(success=False, detail=str(exc), observed_at=now, source=self.provider_id)
        return DeviceResult(
            success=False, detail=f"unknown filesystem service: {command.service!r}", observed_at=now,
            source=self.provider_id,
        )

    def _create_folder(self, params: dict, *, now: datetime) -> DeviceResult:
        target = self._require_within_roots(params["path"])
        if target.exists():
            return DeviceResult(
                success=False, detail=f"already exists: {target}", observed_at=now, source=self.provider_id
            )
        target.mkdir(parents=False)
        verified = target.is_dir()
        return DeviceResult(
            success=verified,
            detail=f"created folder {target}" if verified else f"create_folder did not verify: {target}",
            observed_at=self._clock(),
            source=self.provider_id,
        )

    def _copy(self, params: dict, *, now: datetime) -> DeviceResult:
        source = self._require_within_roots(params["source"])
        destination = self._require_within_roots(params["destination"])
        if not source.is_file():
            return DeviceResult(
                success=False, detail=f"source is not a file: {source}", observed_at=now, source=self.provider_id
            )
        if destination.exists():
            return DeviceResult(
                success=False, detail=f"destination already exists: {destination}", observed_at=now,
                source=self.provider_id,
            )
        shutil.copy2(source, destination)
        verified = destination.is_file() and source.is_file()
        return DeviceResult(
            success=verified,
            detail=f"copied {source} to {destination}" if verified else f"copy did not verify: {destination}",
            observed_at=self._clock(),
            source=self.provider_id,
        )

    def _move(self, params: dict, *, now: datetime) -> DeviceResult:
        source = self._require_within_roots(params["source"])
        destination = self._require_within_roots(params["destination"])
        if not source.exists():
            return DeviceResult(
                success=False, detail=f"source does not exist: {source}", observed_at=now, source=self.provider_id
            )
        if destination.exists():
            return DeviceResult(
                success=False, detail=f"destination already exists: {destination}", observed_at=now,
                source=self.provider_id,
            )
        shutil.move(str(source), str(destination))
        verified = destination.exists() and not source.exists()
        return DeviceResult(
            success=verified,
            detail=f"moved {source} to {destination}" if verified else f"move did not verify: {destination}",
            observed_at=self._clock(),
            source=self.provider_id,
        )

    def _rename(self, params: dict, *, now: datetime) -> DeviceResult:
        source = self._require_within_roots(params["source"])
        destination = self._require_within_roots(source.with_name(params["new_name"]))
        if not source.exists():
            return DeviceResult(
                success=False, detail=f"source does not exist: {source}", observed_at=now, source=self.provider_id
            )
        if destination.exists():
            return DeviceResult(
                success=False, detail=f"destination already exists: {destination}", observed_at=now,
                source=self.provider_id,
            )
        source.rename(destination)
        verified = destination.exists() and not source.exists()
        return DeviceResult(
            success=verified,
            detail=f"renamed {source} to {destination}" if verified else f"rename did not verify: {destination}",
            observed_at=self._clock(),
            source=self.provider_id,
        )


__all__ = [
    "FILESYSTEM_ACTION_RISK",
    "FilesystemProvider",
    "FilesystemProviderConfig",
    "PathOutsideAllowedRoots",
    "PROVIDER_ID",
]
