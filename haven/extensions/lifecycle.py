"""Local lifecycle for host-reviewed, bundled, read-only Feature Modules.

This is a capability boundary for trusted Python, not an OS sandbox. Catalog
claims never select factories or permissions. The composition root supplies
both, and must derive authorization from current identity and scope membership.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4

from .features import FeatureServiceCatalog


class FeatureLifecycleError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class FeatureContext:
    principal_id: str
    scope_id: str

    def __post_init__(self):
        if any(not isinstance(x, str) or not x.strip() for x in (self.principal_id, self.scope_id)):
            raise ValueError("feature context requires a principal and scope")


@dataclass(frozen=True)
class BundledFeatureRelease:
    feature_id: str
    version: str
    display_name: str
    factory: Callable[[], object]
    operations: tuple[str, ...]
    commands: tuple[str, ...]


@dataclass
class _Activation:
    token: str
    module: object
    context: FeatureContext
    valid: bool = True


class FeatureLifecycle:
    """One installation's state, with atomic pointers and revocable handles.

    Staging stores a pending version but changes neither the current version
    nor enablement. Candidate attach/health must succeed before one atomic
    state replacement. Interrupted staging is discarded on the next start.
    """

    def __init__(self, path: Path, *, releases: tuple[BundledFeatureRelease, ...],
                 authorize: Callable[[str, str, str], bool],
                 service_operations: Mapping[str, Callable[..., Any]]):
        self.path = Path(path)
        self._releases = {(x.feature_id, x.version): x for x in releases}
        if len(self._releases) != len(releases):
            raise ValueError("duplicate host feature releases")
        self._authorize = authorize
        self._operations = dict(service_operations)
        self._active: dict[str, _Activation] = {}
        self._lock = RLock()
        self._records = self._load()
        for row in self._records.values():
            if row.pop("pending_version", None):
                row["status"] = "interrupted_update"
        # Persist the recovered pointer before any activation runs.
        self._save(self._records)
        for feature_id, row in list(self._records.items()):
            if row["enabled"]:
                context = FeatureContext(row["principal_id"], row["scope_id"])
                activation = None
                try:
                    self._check(context, "features.enable", feature_id)
                    activation = self._activate(feature_id, row["version"], context)
                    self._check(context, "features.enable", feature_id)
                    self._active[feature_id] = activation
                    row["status"] = "healthy"
                except Exception:
                    if activation:
                        self._retire(activation)
                    row["enabled"] = False
                    row["status"] = "activation_blocked"
        self._save(self._records)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema") != "haven-features.v1" or not isinstance(payload.get("features"), dict):
                raise ValueError("invalid feature state")
            for key, row in payload["features"].items():
                if (not isinstance(row, dict) or not isinstance(row.get("enabled"), bool)
                        or not isinstance(row.get("committed"), bool)):
                    raise ValueError("invalid feature record")
                if any(not isinstance(row.get(x), str) or not row[x] for x in
                       ("version", "principal_id", "scope_id", "status")):
                    raise ValueError("invalid feature record")
                if row["enabled"] and not row["committed"]:
                    raise ValueError("uncommitted feature cannot be enabled")
                if (key, row["version"]) not in self._releases:
                    row["enabled"] = False
                    row["status"] = "release_unavailable"
            return payload["features"]
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise FeatureLifecycleError("state_invalid", "feature state is invalid; repair is required") from exc

    def _save(self, records: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump({"schema": "haven-features.v1", "features": records}, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _commit(self, feature_id: str, row: dict | None) -> None:
        records = {key: dict(value) for key, value in self._records.items()}
        if row is None:
            records.pop(feature_id, None)
        else:
            records[feature_id] = row
        self._save(records)
        self._records = records

    def _check(self, context: FeatureContext, operation: str, feature_id: str | None = None) -> None:
        row = self._records.get(feature_id) if feature_id else None
        if row and (row["principal_id"], row["scope_id"]) != (context.principal_id, context.scope_id):
            raise FeatureLifecycleError("scope_denied", "feature belongs to a different principal or scope")
        try:
            allowed = self._authorize(context.principal_id, context.scope_id, operation) is True
        except Exception:
            allowed = False
        if not allowed:
            if feature_id in self._active:
                self._retire(self._active.pop(feature_id))
                row = dict(self._records[feature_id], enabled=False, status="revoked")
                self._commit(feature_id, row)
            raise FeatureLifecycleError("scope_denied", "current host policy denies this feature operation")

    def _release(self, feature_id: str, version: str) -> BundledFeatureRelease:
        release = self._releases.get((feature_id, version))
        if release is None:
            raise FeatureLifecycleError("untrusted_release", "only host-reviewed bundled releases can run; isolation unavailable")
        return release

    def _row(self, feature_id: str) -> dict:
        if feature_id not in self._records:
            raise FeatureLifecycleError("not_installed", "feature is not installed")
        return dict(self._records[feature_id])

    @staticmethod
    def _retire(activation: _Activation) -> None:
        activation.valid = False
        try:
            activation.module.detach()
        except Exception:
            # Revocation happens first. Detach failure cannot leave a live handle.
            pass

    def _activate(self, feature_id: str, version: str, context: FeatureContext) -> _Activation:
        release = self._release(feature_id, version)
        if any(name not in self._operations for name in release.operations):
            raise FeatureLifecycleError("service_unavailable", "a host service required by this feature is unavailable")
        module = release.factory()
        activation = _Activation(uuid4().hex, module, context)

        def operation(name):
            def invoke(**params):
                with self._lock:
                    if not activation.valid or self._active.get(feature_id) is not activation:
                        raise FeatureLifecycleError("stale_handle", "feature service handle is no longer active")
                    self._check(context, name, feature_id)
                    if any(key in params for key in ("principal_id", "scope_id", "visible_scopes", "permissions")):
                        raise FeatureLifecycleError("scope_denied", "feature cannot supply authority or widen its scope")
                    result = self._operations[name](principal_id=context.principal_id, scope_id=context.scope_id, **params)
                    self._check(context, name, feature_id)
                    return result
            return invoke

        services = FeatureServiceCatalog({name: operation(name) for name in release.operations})
        try:
            if not all(callable(getattr(module, name, None)) for name in ("attach", "health", "detach", "run")):
                raise ValueError("feature requires attach, health, detach and run")
            module.attach(services=services)
            if module.health() is not True:
                raise ValueError("activation health check failed")
        except Exception as exc:
            self._retire(activation)
            raise FeatureLifecycleError("activation_failed", "feature activation health check failed") from exc
        return activation

    def catalog(self, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.list")
            rows = []
            for release in sorted(self._releases.values(), key=lambda x: (x.feature_id, x.version)):
                record = self._records.get(release.feature_id)
                if record and (record["principal_id"], record["scope_id"]) != (context.principal_id, context.scope_id):
                    record = None
                if record and release.feature_id in self._active:
                    self._check(context, "features.status", release.feature_id)
                    record = self._records[release.feature_id]
                rows.append({"feature_id": release.feature_id, "version": release.version,
                             "display_name": release.display_name, "source": "trusted_bundled",
                             "operations": list(release.operations), "commands": list(release.commands),
                             "installed": record is not None,
                             "state": {k: v for k, v in (record or {}).items()
                                       if k not in ("principal_id", "scope_id")}})
            return {"features": rows, "third_party_status": "blocked_isolation_unavailable"}

    def stage(self, feature_id: str, version: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.install", feature_id)
            self._release(feature_id, version)
            row = dict(self._records.get(feature_id) or {
                "version": version, "principal_id": context.principal_id,
                "scope_id": context.scope_id, "enabled": False, "status": "staged", "committed": False})
            if row["version"] == version and row.get("committed", False):
                return {"ok": True, "status": "already_installed", "version": version}
            if not row.get("committed", False):
                row["version"] = version
            row["pending_version"] = version
            self._commit(feature_id, row)
            return {"ok": True, "status": "staged", "version": version}

    def install(self, feature_id: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.install", feature_id)
            row = self._row(feature_id)
            if row.get("committed", False):
                if row.get("pending_version", row["version"]) != row["version"]:
                    raise FeatureLifecycleError("update_required", "use update to replace an installed version")
                return {"ok": True, "status": "already_installed"}
            self._release(feature_id, row["version"])
            row.pop("pending_version", None)
            row.update(committed=True, enabled=False, status="installed_disabled")
            self._commit(feature_id, row)
            return {"ok": True, "status": "installed_disabled"}

    def enable(self, feature_id: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.enable", feature_id)
            row = self._row(feature_id)
            if not row.get("committed", False):
                raise FeatureLifecycleError("not_installed", "commit the staged installation before enabling")
            if feature_id in self._active:
                return {"ok": True, "status": "already_enabled"}
            activation = self._activate(feature_id, row["version"], context)
            try:
                self._check(context, "features.enable", feature_id)
                self._commit(feature_id, dict(row, enabled=True, status="healthy"))
            except Exception:
                self._retire(activation)
                raise
            self._active[feature_id] = activation
            return {"ok": True, "status": "healthy", "version": row["version"]}

    def disable(self, feature_id: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.disable", feature_id)
            row = self._row(feature_id)
            # Invalidate even when disk persistence fails.
            activation = self._active.pop(feature_id, None)
            if activation:
                self._retire(activation)
            self._commit(feature_id, dict(row, enabled=False, status="disabled"))
            return {"ok": True, "status": "disabled"}

    def run(self, feature_id: str, command: str, *, context: FeatureContext, params: dict | None = None) -> dict:
        with self._lock:
            self._check(context, "features.run", feature_id)
            row = self._row(feature_id)
            activation = self._active.get(feature_id)
            if not activation:
                raise FeatureLifecycleError("disabled", "feature is disabled")
            if command not in self._release(feature_id, row["version"]).commands:
                raise FeatureLifecycleError("unknown_command", "feature command is not granted by host policy")
            result = activation.module.run(command, **dict(params or {}))
            self._check(context, "features.run", feature_id)
            return {"ok": True, "status": "completed", "feature_id": feature_id,
                    "version": row["version"], "result": result}

    def update(self, feature_id: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.update", feature_id)
            row = self._row(feature_id)
            version = row.get("pending_version")
            if not row.get("committed", False) or not version:
                raise FeatureLifecycleError("not_staged", "stage an update before switching versions")
            self._release(feature_id, version)
            if version == row["version"]:
                row.pop("pending_version", None)
                self._commit(feature_id, row)
                return {"ok": True, "status": "already_installed", "version": version}
            # Health-check every update, including one to a disabled feature.
            candidate = self._activate(feature_id, version, context)
            new = dict(row, version=version, previous_version=row["version"],
                       status="healthy" if row["enabled"] else "installed_disabled")
            new.pop("pending_version", None)
            try:
                self._check(context, "features.update", feature_id)
                self._commit(feature_id, new)
            except Exception:
                self._retire(candidate)
                raise
            prior = self._active.pop(feature_id, None)
            if row["enabled"]:
                self._active[feature_id] = candidate
            else:
                self._retire(candidate)
            if prior:
                self._retire(prior)
            return {"ok": True, "status": new["status"], "version": version}

    def rollback(self, feature_id: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.rollback", feature_id)
            row = self._row(feature_id)
            previous = row.get("previous_version")
            if not previous:
                raise FeatureLifecycleError("rollback_unavailable", "no previous healthy version is retained")
            self._commit(feature_id, dict(row, pending_version=previous))
            return self.update(feature_id, context=context)

    def uninstall(self, feature_id: str, *, context: FeatureContext) -> dict:
        with self._lock:
            self._check(context, "features.uninstall", feature_id)
            self._row(feature_id)
            activation = self._active.pop(feature_id, None)
            if activation:
                self._retire(activation)
            self._commit(feature_id, None)
            return {"ok": True, "status": "uninstalled"}

    def close(self) -> None:
        with self._lock:
            for activation in self._active.values():
                self._retire(activation)
            self._active.clear()


class DecisionContextInspector:
    """A bundled offline read-only feature; knowledge remains non-authoritative."""

    def attach(self, *, services: FeatureServiceCatalog) -> None:
        self._services = services

    def health(self) -> bool:
        return self._services.names == ("decisions.context",)

    def detach(self) -> None:
        self._services = None

    def run(self, command: str, *, decision_id: str | None = None, limit: int = 10) -> dict:
        if command != "inspect" or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 12:
            raise ValueError("inspect requires a limit between 1 and 12")
        if decision_id is not None and (not isinstance(decision_id, str) or not decision_id.strip()):
            raise ValueError("decision_id must be a non-empty string")
        return self._services.call("decisions.context", decision_id=decision_id, limit=limit)


class DecisionContextInspectorV11(DecisionContextInspector):
    """Smaller default read budget, retaining the identical service boundary."""

    def run(self, command: str, *, decision_id: str | None = None, limit: int = 8) -> dict:
        return super().run(command, decision_id=decision_id, limit=limit)


def bundled_feature_releases() -> tuple[BundledFeatureRelease, ...]:
    return (
        BundledFeatureRelease("haven.decision-context", "1.0.0", "Decision context inspector",
                              DecisionContextInspector, ("decisions.context",), ("inspect",)),
        BundledFeatureRelease("haven.decision-context", "1.1.0", "Decision context inspector",
                              DecisionContextInspectorV11, ("decisions.context",), ("inspect",)),
    )
