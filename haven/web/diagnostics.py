"""System diagnostics and backup/restore for the local web surface.

Diagnostics are a read-only snapshot of one installation's health: which
world is running, what the setup wizard has collected, and how many rules,
events, and models the runtime holds. Collection never touches the network
and never surfaces token material or file contents, so the System view can
render it on every open. The provider probe is the one deliberate exception:
it is a separate, user-invoked reachability check against the attached
Home Assistant states source.

Backup is honest about its boundary: a backup is a copy of every file that
makes up one installation (`installation_file_names` -- setup config,
provider token, household declarations, enrolled devices, automations,
    durable history, resources, ontology, admitted knowledge, and every
    provider's own config).
Restoring copies them back, but rules, household, and enrollments load at
boot, so the running process keeps its in-memory state until a restart --
the restore response says so.
"""

from __future__ import annotations

import shutil
import os
import subprocess
import time
import re
import json
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as _package_version_lookup
from pathlib import Path

from ..core.domain import RuleStatus
from ..audit.unified_receipt import from_action_receipt, from_ledger_entry
from ..ipc.named_pipe import installation_id_for_data_dir
from ..models import ModelState
from .installation_files import installation_file_names
from .setup_service import _ENROLLED_FILENAME, load_enrolled_sidecar

_ONE_SECOND = timedelta(seconds=1)
_RECEIPT_EXPORT_LIMIT = 20


def _copy_file(source: Path, destination: Path) -> None:
    # CopyFile2 otherwise rejects deeply nested local product backups even
    # when Python successfully created their directories.
    def native(path):
        value = str(path.absolute())
        if os.name != "nt" or value.startswith("\\\\?\\"):
            return value
        if value.startswith("\\\\"):
            return "\\\\?\\UNC\\" + value[2:]
        return "\\\\?\\" + value
    shutil.copy2(native(source), native(destination))


def _package_version() -> str | None:
    try:
        return _package_version_lookup("haven-household")
    except PackageNotFoundError:
        return None


def _git_commit(repo_root: Path) -> str | None:
    """Best-effort only: a packaged/non-git install has no `.git` to ask,
    and that is not a diagnostic failure worth surfacing as one."""

    manifest = repo_root / "bundle-manifest.json"
    if manifest.is_file():
        try:
            commit = json.loads(manifest.read_text(encoding="utf-8"))["source_commit"]
            return commit if isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None
    # A package nested inside a checkout must never inherit its parent's SHA.
    if not (repo_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


class SystemDiagnostics:
    """Collects the diagnostics payload for one ``HavenWebServer``."""

    def __init__(self, *, server, provider_health_listener=None) -> None:
        self._server = server
        self._provider_health_listener = provider_health_listener

    def collect(self) -> dict:
        server = self._server
        director = server.director
        setup = server.setup
        status = setup.status()
        rules = director.store.state.rules
        now = director._clock()  # private clock: the director owns its time base
        schedule_rows = director.scheduler.status(
            rules, world=director.world.observe(now), now=now
        )
        enrolled = load_enrolled_sidecar(server.setup_store.path.parent / _ENROLLED_FILENAME)
        records = server.models.list_models()
        claim_counts = server.claims.count_by_state()
        return {
            "ok": True,
            "diagnostics": {
                "data_dir": str(server.setup_store.path.parent),
                "config_error": status.get("config_error"),
                "world": {
                    "mode": (
                        "demo"
                        if director.house is not None
                        else "home_assistant"
                        if director.ha_states_source is not None
                        else "local"
                    )
                },
                "provider": status["setup"]["provider"],
                "household": {
                    "people": len(setup.household.people),
                    "contexts": len(setup.household.contexts),
                },
                "devices": {
                    "enrolled": len(enrolled.manifests),
                    "registered": len(tuple(director.registry.all_devices())),
                },
                "rules": {
                    "total": len(rules),
                    "approved": sum(1 for rule in rules if rule.status is RuleStatus.APPROVED),
                    "proposed": sum(1 for rule in rules if rule.status is RuleStatus.PROPOSED),
                },
                "scheduler": {
                    "entries": len(schedule_rows),
                    "enabled": sum(1 for row in schedule_rows if row.enabled),
                },
                "events": len(director.store.events),
                "knowledge": {
                    "total": sum(claim_counts.values()),
                    "current": sum(
                        count for state, count in claim_counts.items()
                        if state not in ("stale", "unavailable")
                    ),
                    "disputed": claim_counts.get("disputed", 0),
                    "stale": claim_counts.get("stale", 0),
                    "by_state": claim_counts,
                },
                "models": {
                    "registered": len(records),
                    "loaded": sum(1 for record in records if record.state is ModelState.LOADED),
                    "assigned_roles": sum(
                        1 for model_id in server.models.assignments().values() if model_id
                    ),
                },
                "voice": {
                    "enabled": director.voice_enabled,
                    "state": director.voice.to_dict()["state"],
                },
                "uptime_seconds": time.monotonic() - server._started_monotonic,
            },
        }

    def receipts(self) -> dict:
        """Private owner view, separate from the shareable support projection."""
        from ..core.domain import RoleTier

        server = self._server
        if not server.director.has_declared_owner or server.identity.current_principal().role_tier is not RoleTier.OWNER:
            return {"ok": False, "error": "owner_required"}
        if server.director.household_id not in server.identity.visible_scope_ids():
            return {"ok": False, "error": "scope_denied"}
        home = [from_action_receipt(row).to_dict() for row in getattr(server.director, "receipts", ())]
        resource = [from_ledger_entry(row, domain="resource").to_dict()
                    for row in server.action_ledger.list_by_household(
                        server.director.household_id, limit=_RECEIPT_EXPORT_LIMIT)]
        rows = sorted(home + resource, key=lambda row: row.get("recorded_at", ""), reverse=True)
        return {"ok": True, "unified_receipts": rows[:_RECEIPT_EXPORT_LIMIT]}

    def export(self) -> dict:
        """Allowlisted support summary. Local visibility is not export consent.

        Never serialize IDs, paths, endpoint/config strings, assignments or
        receipt fields. The separate owner receipts view retains local detail.
        Unknown fields and future domain additions are omitted by construction.
        """
        base = self.collect()["diagnostics"]

        def count(value):
            return min(value, 2**31 - 1) if type(value) is int and value >= 0 else 0

        def counts(section, keys):
            row = base.get(section, {})
            return {key: count(row.get(key)) for key in keys}

        mode = base.get("world", {}).get("mode")
        mode = mode if mode in {"local", "demo", "home_assistant"} else "unknown"
        version = _package_version()
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}(?:[-+][A-Za-z0-9.]+)?", version):
            version = None
        commit = _git_commit(Path(__file__).resolve().parents[2])
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40,64}", commit):
            commit = None
        receipts = self.receipts().get("unified_receipts", [])
        summary = {"total": len(receipts), "executed": 0, "failed": 0, "not_executed": 0}
        for row in receipts:
            key = "executed" if row.get("execution_success") is True else (
                "failed" if row.get("execution_success") is False else "not_executed")
            summary[key] += 1
        try:
            incidents = self._server.faults.summary()
        except (OSError, ValueError, KeyError, TypeError):
            from ..faults import public_failure
            return public_failure({"ok": False, "error": "export_redaction_failed"}, "system.diagnostics.export")
        return {"ok": True, "export": {
            "schema_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": version, "commit": commit,
            "diagnostics": {
                "world": {"mode": mode},
                "config_valid": base.get("config_error") is None,
                "provider": {"configured": base.get("provider", {}).get("configured") is True},
                "household": counts("household", ("people", "contexts")),
                "devices": counts("devices", ("enrolled", "registered")),
                "rules": counts("rules", ("total", "approved", "proposed")),
                "scheduler": counts("scheduler", ("entries", "enabled")),
                "events": count(base.get("events")),
                "knowledge": counts("knowledge", ("total", "current", "disputed", "stale")),
                "models": counts("models", ("registered", "loaded", "assigned_roles")),
                "voice": {"enabled": base.get("voice", {}).get("enabled") is True},
            },
            "receipt_summary": summary,
            "error_schema_version": 1,
            "incidents": incidents,
            "event_channel": "reported client-side only",
        }}

    def probe_provider(self) -> dict:
        source = self._server.director.ha_states_source
        if source is None:
            return {"ok": False, "error": "no provider attached"}
        try:
            states = source.fetch_states()
        except Exception as exc:
            if self._provider_health_listener is not None:
                try:
                    self._provider_health_listener(
                        provider_id="home_assistant",
                        reachable=False,
                        detail=f"{type(exc).__name__}: {exc}",
                        occurred_at=datetime.now(timezone.utc),
                    )
                except Exception:
                    pass
            from ..faults import public_failure
            failure = public_failure({"ok": False, "error": "provider_unavailable", "detail": str(exc)},
                                     "providers.probe", journal=self._server.faults)
            return {"ok": True, "reachable": False, "detail": failure["fault"]["message"], "fault": failure["fault"]}

        if self._provider_health_listener is not None:
            try:
                self._provider_health_listener(
                    provider_id="home_assistant",
                    reachable=True,
                    detail=f"{len(states)} states fetched",
                    occurred_at=datetime.now(timezone.utc),
                )
            except Exception:
                pass
        return {"ok": True, "reachable": True, "detail": f"{len(states)} states fetched"}


class BackupManager:
    """Copies the installation's files into timestamped backups and back.

    Every file `installation_file_names` knows about -- config, provider
    token, household/enrolled/automations sidecars, durable history,
    resources, ontology, the resource action ledger, the computer
    provider's own config, and every installed community provider's
    config/secrets sidecars -- not a hardcoded five; the same
    single-source-of-truth list `choose_data_dir` uses to move the
    installation, so the two can never drift apart the way a second
    hardcoded tuple here already once did.
    """

    def __init__(self, *, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)

    def create(self) -> dict:
        now = datetime.now(timezone.utc)
        # Second-resolution ids collide when two backups land in the same
        # second; bump forward to the next free stamp instead of failing.
        while True:
            backup_id = now.strftime("%Y%m%dT%H%M%SZ")
            target = self._backups_dir() / backup_id
            if not target.exists():
                break
            now += _ONE_SECOND
        target.mkdir(parents=True, exist_ok=False)
        files = []
        for name in installation_file_names(self._data_dir):
            source = self._data_dir / name
            if not source.exists():
                continue
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            _copy_file(source, target / name)
            files.append(name)
        return {"id": backup_id, "created_at": now.isoformat(), "files": files}

    def list(self) -> dict:
        backups_dir = self._backups_dir()
        if not backups_dir.is_dir():
            return {"backups": []}
        entries = []
        for entry in backups_dir.iterdir():
            if not entry.is_dir():
                continue
            created_at = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            entries.append(
                {
                    "id": entry.name,
                    "files": sorted(child.name for child in entry.iterdir() if child.is_file()),
                    "created_at": created_at.isoformat(),
                }
            )
        entries.sort(key=lambda item: item["id"], reverse=True)
        return {"backups": entries}

    def restore(self, backup_id: str) -> dict:
        """Copy a backup's files back over the data-dir sidecars.

        The copy is real, but the boundary is stated plainly: rules,
        household declarations, and enrollments load at boot, so the running
        process keeps its in-memory state until a restart. Only preferences
        and the provider config, which the setup service rereads, take effect
        immediately.
        """

        source = self._require_backup_dir(backup_id)
        restored = []
        # Restore whatever this specific backup actually holds, not
        # `installation_file_names` re-evaluated against the *live* data
        # dir: a per-provider config/secrets sidecar the live install has
        # today may not be what was present when this backup was taken (a
        # provider installed since, or uninstalled since), and the backup
        # snapshot itself is the authority on what it contains.
        for name in installation_file_names(source):
            candidate = source / name
            if not candidate.is_file():
                continue
            destination = self._data_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(candidate, destination)
            restored.append(name)
        return {"restored": restored, "restart_required": True}

    def delete(self, backup_id: str) -> dict:
        shutil.rmtree(self._require_backup_dir(backup_id))
        return {"deleted": backup_id}

    def _backups_dir(self) -> Path:
        return self._data_dir / "backups"

    def _require_backup_dir(self, backup_id: str) -> Path:
        if not isinstance(backup_id, str) or not backup_id:
            raise ValueError("unknown backup")
        # A backup id is one path component: anything with a separator, an
        # absolute path, or a dot-segment escapes the backups root.
        if backup_id in (".", "..") or Path(backup_id).name != backup_id:
            raise ValueError("unknown backup")
        target = self._backups_dir() / backup_id
        if not target.is_dir():
            raise ValueError("unknown backup")
        return target


__all__ = ["BackupManager", "SystemDiagnostics"]
