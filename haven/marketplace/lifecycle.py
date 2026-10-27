"""Local, inert product acquisition under host-owned trust.

Catalog metadata never supplies a path, command, import, or adapter factory.
Only releases explicitly pinned by the host can enter this lifecycle. The
first adapter renders a JSON welcome card; executable/archive installation
and commercial transactions deliberately have no implementation here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .catalog_client import CatalogFetchError, parse_and_verify_catalog
from .contracts import MarketplaceReleaseState, safe_product_id

_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_BYTES = 256 * 1024
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class ProductError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LocalRelease:
    """Host configuration, never deserialized from catalog/request data."""

    product_id: str
    version: str
    platform: str
    sha256: str
    size_bytes: int
    source: Path
    adapter_id: str = "welcome-card"
    dependencies: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if safe_product_id(self.product_id) != self.product_id:
            raise ValueError("product_id must be canonical")
        if not _VERSION.fullmatch(self.version):
            raise ValueError("version must be an exact numeric x.y.z release")
        if not _DIGEST.fullmatch(self.sha256):
            raise ValueError("host release requires a sha256 pin")
        if type(self.size_bytes) is not int or not 0 < self.size_bytes <= _MAX_BYTES:
            raise ValueError("host release size is outside the local content limit")
        if self.adapter_id != "welcome-card":
            raise ValueError("no approved product adapter exists")
        for product, version in self.dependencies:
            safe_product_id(product)
            if not _VERSION.fullmatch(version):
                raise ValueError("dependencies require exact versions")


def _plain_path(path: Path) -> None:
    """Reject symlinks, junctions and reparse points before local I/O."""
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ProductError("unsafe_path", "product storage cannot traverse a link")
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ProductError("unsafe_path", "product storage cannot traverse a reparse point")


class LocalProductManager:
    """Atomic local content lifecycle with live authorization and verification.

    ``catalog`` returns the current signed envelope; ``trusted_keys`` returns
    current host trust on every operation, so key removal and catalog security
    holds defeat old state. ``authorize`` must consult current identity/consent.
    No permission decision is saved in the installation registry.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        catalog: Callable[[], bytes | dict[str, Any]],
        trusted_keys: Callable[[], Mapping[str, str]],
        releases: tuple[LocalRelease, ...],
        platform: str,
        authorize: Callable[[str, str], bool],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root).absolute()
        if os.name == "nt" and not str(self.root).startswith("\\\\?\\"):
            value = str(self.root)
            self.root = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
        _plain_path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._catalog = catalog
        self._trusted_keys = trusted_keys
        self._releases = {(r.product_id, r.version): r for r in releases}
        if len(self._releases) != len(releases):
            raise ValueError("host release pins must be unique")
        self._platform = platform
        self._authorize = authorize
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._healthy: dict[str, str] = {}
        self._cancel_guard = threading.Lock()
        self._operations: dict[str, tuple[str, threading.Event]] = {}
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.root).casefold(), threading.RLock())
        with self._transaction():
            # A staged candidate is never an installed product. OS-level locks
            # release on process death; these files are safe to discard on boot.
            staging = self._path("staging")
            if staging.exists():
                self._check_tree(staging)
                shutil.rmtree(staging)
            for directory in (self.root, self._path("downloads")):
                if directory.exists():
                    for temporary in directory.glob("*.tmp"):
                        _plain_path(temporary)
                        temporary.unlink()
            self._cleanup(self._load())

    def _path(self, *parts: str) -> Path:
        result = self.root.joinpath(*parts)
        if not result.is_relative_to(self.root):
            raise ProductError("unsafe_path", "product path escaped storage")
        _plain_path(result)
        return result

    @contextmanager
    def _transaction(self):
        with self._lock:
            path = self._path("registry.lock")
            with path.open("a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ProductError("busy", "another product operation is active") from exc
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _allowed(self, principal_id: str, operation: str) -> None:
        try:
            allowed = isinstance(principal_id, str) and bool(principal_id) and self._authorize(principal_id, operation) is True
        except Exception:
            allowed = False
        if not allowed:
            raise ProductError("forbidden", "current local product authorization is required")

    def _load(self) -> dict[str, Any]:
        path = self._path("registry.json")
        if not path.exists():
            return {"schema_version": "haven-installed-products.v1", "products": {}}
        try:
            raw = json.loads(path.read_bytes())
            if raw["schema_version"] != "haven-installed-products.v1" or not isinstance(raw["products"], dict):
                raise ValueError()
            for product_id, value in raw["products"].items():
                if safe_product_id(product_id) != product_id or not isinstance(value, dict):
                    raise ValueError()
                if type(value.get("enabled", False)) is not bool:
                    raise ValueError()
                if "removal_principal_id" in value and (not isinstance(value["removal_principal_id"], str) or not value["removal_principal_id"]):
                    raise ValueError()
                for version in value.get("versions", []):
                    if (product_id, version) not in self._releases:
                        raise ValueError()
                if value.get("installed_version") is not None and value["installed_version"] not in value.get("versions", []):
                    raise ValueError()
            return raw
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProductError("registry_invalid", "installed product state is unreadable; preserved for recovery") from exc

    def _save(self, state: dict[str, Any]) -> None:
        raw = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
        self._atomic_bytes(self._path("registry.json"), raw)

    def _atomic_bytes(self, destination: Path, raw: bytes) -> None:
        _plain_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Do not append the long digest filename: common Windows data roots
        # otherwise exceed MAX_PATH during an atomic download write.
        temporary = destination.with_name(uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _release(self, product_id: str, version: str) -> LocalRelease:
        if safe_product_id(product_id) != product_id or not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ProductError("release_invalid", "an exact product and version are required")
        release = self._releases.get((product_id, version))
        if release is None:
            raise ProductError("release_not_approved", "release is not pinned by the host")
        if release.platform != self._platform:
            raise ProductError("platform_mismatch", "release does not match this host platform")
        try:
            snapshot = parse_and_verify_catalog(self._catalog(), trusted_keys=self._trusted_keys(), now=self._clock())
        except CatalogFetchError as exc:
            raise ProductError(exc.code, str(exc)) from exc
        item = next((item for item in snapshot.items if item.product_id == product_id), None)
        if item is None or item.release_state not in {MarketplaceReleaseState.AVAILABLE, MarketplaceReleaseState.EARLY_ACCESS}:
            raise ProductError("release_revoked", "product is absent or is not currently available")
        if item.execution is None or item.execution.value != "content_only" or item.publisher != "The Hidden Canopy LLC":
            raise ProductError("release_not_approved", "only first-party local content is approved")
        matches = [d for d in item.distribution if d.release_id == version]
        if len(matches) != 1:
            raise ProductError("version_mismatch", "catalog does not pin exactly one matching version")
        distribution = matches[0]
        if distribution.kind.value != "local_content" or distribution.platforms != (self._platform,):
            raise ProductError("platform_mismatch", "catalog distribution is not local content for this platform")
        if distribution.sha256 != release.sha256 or distribution.size_bytes != release.size_bytes:
            raise ProductError("digest_mismatch", "catalog release does not match the host pin")
        return release

    def _bytes(self, path: Path, release: LocalRelease) -> bytes:
        _plain_path(path)
        if not path.is_file() or path.stat().st_size != release.size_bytes:
            raise ProductError("artifact_invalid", "local artifact is missing or has the wrong size")
        with path.open("rb") as handle:
            raw = handle.read(release.size_bytes + 1)
        if len(raw) != release.size_bytes or hashlib.sha256(raw).hexdigest() != release.sha256:
            raise ProductError("digest_mismatch", "local artifact digest does not match the pinned release")
        return raw

    def _inspect(self, raw: bytes, release: LocalRelease) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
            if set(payload) != {"schema_version", "product_id", "version", "platform", "adapter_id", "message"}:
                raise ValueError()
            if payload["schema_version"] != "haven-local-content.v1" or any(
                payload[key] != getattr(release, key)
                for key in ("product_id", "version", "platform", "adapter_id")
            ):
                raise ValueError()
            if not isinstance(payload["message"], str) or not 0 < len(payload["message"]) <= 4000:
                raise ValueError()
            return payload
        except (ValueError, TypeError, KeyError) as exc:
            raise ProductError("artifact_invalid", "local product is not approved inert content metadata") from exc

    def _download_path(self, release: LocalRelease) -> Path:
        return self._path("downloads", release.sha256 + ".json")

    def _installed_path(self, release: LocalRelease) -> Path:
        return self._path("releases", release.product_id, release.version, "package.json")

    def _dependencies(self, release: LocalRelease, state: dict[str, Any]) -> None:
        for product_id, version in release.dependencies:
            dependency = self._release(product_id, version)
            if state["products"].get(product_id, {}).get("installed_version") != version:
                raise ProductError("dependency_missing", "an approved exact product dependency is not installed")
            self._inspect(self._bytes(self._installed_path(dependency), dependency), dependency)

    def cancel(self, operation_id: str, *, principal_id: str) -> dict[str, Any]:
        self._allowed(principal_id, "cancel")
        with self._cancel_guard:
            running = self._operations.get(operation_id)
            if running is not None and running[0] == principal_id:
                running[1].set()
                return {"ok": True, "cancel_requested": True}
        return {"ok": True, "cancel_requested": False}

    def download(self, product_id: str, version: str, *, principal_id: str, operation_id: str | None = None) -> dict[str, Any]:
        self._allowed(principal_id, "download")
        operation_id = operation_id or uuid.uuid4().hex
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", operation_id):
            raise ProductError("operation_invalid", "operation id must be a safe correlation identifier")
        cancelled = threading.Event()
        with self._cancel_guard:
            if operation_id in self._operations:
                raise ProductError("busy", "operation id is already active")
            self._operations[operation_id] = (principal_id, cancelled)
        try:
            with self._transaction():
                release = self._release(product_id, version)
                destination = self._download_path(release)
                if destination.exists():
                    self._inspect(self._bytes(destination, release), release)
                else:
                    _plain_path(release.source)
                    if not release.source.is_file() or release.source.stat().st_size != release.size_bytes:
                        raise ProductError("artifact_invalid", "host-pinned source is missing or has the wrong size")
                    chunks = []
                    with release.source.open("rb") as source:
                        remaining = release.size_bytes
                        while remaining:
                            if cancelled.is_set():
                                raise ProductError("cancelled", "product download was cancelled")
                            chunk = source.read(min(4096, remaining))
                            if not chunk:
                                raise ProductError("interrupted", "product source ended before the pinned size")
                            chunks.append(chunk)
                            remaining -= len(chunk)
                        if source.read(1):
                            raise ProductError("artifact_invalid", "product source exceeds the pinned size")
                    raw = b"".join(chunks)
                    if hashlib.sha256(raw).hexdigest() != release.sha256:
                        raise ProductError("digest_mismatch", "product download digest does not match the host pin")
                    self._inspect(raw, release)
                    self._allowed(principal_id, "download")
                    self._release(product_id, version)
                    if cancelled.is_set():
                        raise ProductError("cancelled", "product download was cancelled")
                    self._atomic_bytes(destination, raw)
                return self._status_unlocked(product_id, self._load())
        finally:
            with self._cancel_guard:
                self._operations.pop(operation_id, None)

    def install(self, product_id: str, version: str, *, principal_id: str) -> dict[str, Any]:
        self._allowed(principal_id, "install")
        with self._transaction():
            return self._install(product_id, version, principal_id=principal_id, rollback=False)

    def _install(self, product_id: str, version: str, *, principal_id: str, rollback: bool) -> dict[str, Any]:
        release = self._release(product_id, version)
        state = self._load()
        product = state["products"].get(product_id, {})
        if product.get("removal_pending"):
            raise ProductError("removal_pending", "finish product removal before reinstalling")
        if rollback and version not in product.get("versions", []):
            raise ProductError("rollback_unavailable", "rollback requires a previously installed exact version")
        self._dependencies(release, state)
        source = self._installed_path(release) if rollback else self._download_path(release)
        if not source.exists():
            raise ProductError("download_required", "download and verify the exact release before installation")
        raw = self._bytes(source, release)
        self._inspect(raw, release)
        target = self._installed_path(release)
        if target.exists():
            self._inspect(self._bytes(target, release), release)
        else:
            stage = self._path("staging", uuid.uuid4().hex)
            stage.mkdir(parents=True)
            try:
                self._atomic_bytes(stage / "package.json", raw)
                self._allowed(principal_id, "rollback" if rollback else "install")
                self._release(product_id, version)
                target.parent.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage, target.parent)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        # Re-read authority immediately before changing the durable pointer.
        self._allowed(principal_id, "rollback" if rollback else "install")
        self._release(product_id, version)
        if product.get("installed_version") != version:
            versions = list(dict.fromkeys([*product.get("versions", []), version]))
            state["products"][product_id] = {"installed_version": version, "versions": versions, "enabled": False, "activation_id": uuid.uuid4().hex, "data_preserved": True}
            self._save(state)
            self._healthy.pop(product_id, None)
        return self._status_unlocked(product_id, state)

    def rollback(self, product_id: str, version: str, *, principal_id: str) -> dict[str, Any]:
        self._allowed(principal_id, "rollback")
        with self._transaction():
            return self._install(product_id, version, principal_id=principal_id, rollback=True)

    def set_enabled(self, product_id: str, enabled: bool, *, principal_id: str) -> dict[str, Any]:
        self._allowed(principal_id, "enable" if enabled else "disable")
        if type(enabled) is not bool:
            raise ProductError("request_invalid", "enabled must be a boolean")
        with self._transaction():
            state = self._load()
            product = state["products"].get(safe_product_id(product_id), {})
            version = product.get("installed_version")
            if version is None:
                raise ProductError("not_installed", "product is not installed")
            if enabled:
                release = self._release(product_id, version)
                self._dependencies(release, state)
                self._inspect(self._bytes(self._installed_path(release), release), release)
            if product.get("enabled") != enabled:
                self._allowed(principal_id, "enable" if enabled else "disable")
                product["enabled"] = enabled
                product["activation_id"] = uuid.uuid4().hex
                self._save(state)
                self._healthy.pop(product_id, None)
            return self._status_unlocked(product_id, state)

    def launch(self, product_id: str, *, principal_id: str) -> dict[str, Any]:
        self._allowed(principal_id, "launch")
        with self._transaction():
            state = self._load()
            product = state["products"].get(safe_product_id(product_id), {})
            version = product.get("installed_version")
            if version is None or not product.get("enabled"):
                raise ProductError("not_enabled", "install and explicitly enable the product before opening it")
            try:
                release = self._release(product_id, version)
                self._dependencies(release, state)
                content = self._inspect(self._bytes(self._installed_path(release), release), release)
            except Exception:
                self._healthy.pop(product_id, None)
                raise
            # A fixed host-owned adapter. No import, subprocess, shell, or
            # feature enablement can be selected by product metadata.
            self._allowed(principal_id, "launch")
            self._healthy[product_id] = product["activation_id"]
            return {"ok": True, "adapter_id": "welcome-card", "product_id": product_id, "version": version, "message": content["message"], "observed_healthy": True}

    def _check_tree(self, path: Path) -> None:
        _plain_path(path)
        for child in path.rglob("*"):
            _plain_path(child)

    def _cleanup(self, state: dict[str, Any]) -> None:
        changed = False
        for product_id, product in state["products"].items():
            if not product.get("removal_pending"):
                continue
            # A recovery checkpoint is intent, not permission. Legacy
            # checkpoints with no actor require an explicit new uninstall.
            principal_id = product.get("removal_principal_id")
            try:
                self._allowed(principal_id, "uninstall")
            except ProductError:
                continue
            for location in (self._path("releases", product_id),):
                if location.exists():
                    self._check_tree(location)
                    self._allowed(principal_id, "uninstall")
                    shutil.rmtree(location)
            for release in self._releases.values():
                if release.product_id == product_id:
                    self._allowed(principal_id, "uninstall")
                    self._download_path(release).unlink(missing_ok=True)
            if not product["data_preserved"]:
                location = self._path("data", product_id)
                if location.exists():
                    self._check_tree(location)
                    self._allowed(principal_id, "uninstall")
                    shutil.rmtree(location)
            product["removal_pending"] = False
            changed = True
        if changed:
            self._save(state)

    def uninstall(self, product_id: str, *, principal_id: str, data_policy: str = "preserve") -> dict[str, Any]:
        self._allowed(principal_id, "uninstall")
        if data_policy not in {"preserve", "delete"}:
            raise ProductError("request_invalid", "data_policy must explicitly be preserve or delete")
        safe_product_id(product_id)
        with self._transaction():
            state = self._load()
            dependents = [key for key, value in state["products"].items() if value.get("installed_version") and any(dep[0] == product_id for dep in self._releases[(key, value["installed_version"])].dependencies)]
            if dependents:
                raise ProductError("dependency_in_use", "remove dependent products before removing this product")
            self._allowed(principal_id, "uninstall")
            state["products"][product_id] = {"installed_version": None, "versions": [], "enabled": False, "data_preserved": data_policy == "preserve", "removal_pending": True, "removal_principal_id": principal_id}
            self._save(state)
            self._healthy.pop(product_id, None)
            try:
                self._cleanup(state)
            except OSError as exc:
                raise ProductError("removal_pending", "product is disabled and unregistered; file cleanup will resume on restart") from exc
            return self._status_unlocked(product_id, state)

    def _status_unlocked(self, product_id: str, state: dict[str, Any]) -> dict[str, Any]:
        safe_product_id(product_id)
        product = state["products"].get(product_id, {})
        releases = []
        for (key, version), release in self._releases.items():
            if key != product_id:
                continue
            error = None
            try:
                self._release(product_id, version)
            except (ProductError, OSError) as exc:
                error = getattr(exc, "code", "catalog_unavailable")
            downloaded = False
            try:
                self._inspect(self._bytes(self._download_path(release), release), release)
                downloaded = True
            except (ProductError, OSError):
                pass
            releases.append({"version": version, "platform": release.platform, "available": error is None, "downloaded": downloaded, "error": error})
        version = product.get("installed_version")
        available = next((r["available"] for r in releases if r["version"] == version), False)
        integrity = False
        dependencies_ready = False
        if version is not None:
            try:
                release = self._releases[(product_id, version)]
                self._inspect(self._bytes(self._installed_path(release), release), release)
                integrity = True
                self._dependencies(release, state)
                dependencies_ready = True
            except (ProductError, OSError):
                pass
        enabled = bool(product.get("enabled") and version is not None and available and integrity and dependencies_ready)
        if not enabled:
            self._healthy.pop(product_id, None)
        return {"product_id": product_id, "releases": releases, "installed": version is not None, "installed_version": version, "installed_integrity_verified": integrity, "dependencies_satisfied": dependencies_ready, "enabled": enabled, "enablement_requested": bool(product.get("enabled")), "observed_healthy": enabled and self._healthy.get(product_id) == product.get("activation_id"), "rollback_versions": [v for v in product.get("versions", []) if v != version], "data_preserved": product.get("data_preserved", True), "removal_pending": product.get("removal_pending", False), "integration_enabled": False}

    def status(self, product_id: str) -> dict[str, Any]:
        with self._transaction():
            return self._status_unlocked(product_id, self._load())

    def view(self) -> dict[str, Any]:
        with self._transaction():
            state = self._load()
            product_ids = sorted({r.product_id for r in self._releases.values()} | set(state["products"]))
            return {"ok": True, "products": [self._status_unlocked(product_id, state) for product_id in product_ids], "local_only": True, "commerce_available": False}


__all__ = ["LocalProductManager", "LocalRelease", "ProductError"]
