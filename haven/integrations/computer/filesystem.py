"""`FilesystemProvider`: the first real "computer" resource provider.

Satisfies `haven.perception.observation.ObservationProvider` for reading
(one `haven.resources.ResourceRecord` per file/folder under an explicitly
allowed root) and `haven.execution.registry.ExecutionAdapter` for writing
(create-folder/copy/move/rename), so it plugs into the exact same seams a
Home Assistant or community provider does -- `CompositeObserver` for
observation, `ExecutionProviderRegistry` for execution -- nothing here is a
special case Haven Core needs to know about.

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
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from haven.core.domain import DeviceCommand, DeviceResult, RiskTier
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
    "filesystem.create_folder": RiskTier.SAFE_AUTOMATIC,
    "filesystem.copy": RiskTier.SAFE_AUTOMATIC,
    "filesystem.move": RiskTier.CONFIRMATION_REQUIRED,
    "filesystem.rename": RiskTier.CONFIRMATION_REQUIRED,
}


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


@dataclass(frozen=True)
class FilesystemProviderConfig:
    """What a household explicitly declared, never inferred."""

    allowed_roots: tuple[Path, ...]
    scope_id: str
    read_only: bool = False
    max_entries: int = _DEFAULT_MAX_ENTRIES


class FilesystemProvider:
    """Observes and (optionally) mutates files under explicitly allowed roots."""

    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        allowed_roots: Iterable[str | Path],
        scope_id: str,
        read_only: bool = False,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Callable[[], datetime] | None = None,
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
        capabilities = ["filesystem.read"]
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

    def execute(self, command: DeviceCommand) -> DeviceResult:
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
