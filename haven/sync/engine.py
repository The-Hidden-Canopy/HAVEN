"""`LocalSyncEngine`: the first `SyncProvider`-shaped implementation.

Producers call `record_mutation` on every synced mutation; the engine
appends a `SyncEvent` (origin device id, object id, revision + causal
parents, scope id, event id) to the outbox. `push`/`pull` move events
across a transport behind the two-method seam. Applying a pulled event
never does silent last-write-wins: a divergent revision becomes a durable
`SyncConflict` requiring review -- resolve by choosing A, choosing B, or
merging fields where legal.

Sync is off by default; a single-device installation loses nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..credentials import CredentialKind, CredentialStore, UnknownCredentialError
from .events import SyncEvent, device_id_for, is_syncable
from .store import SyncEventStore
from .transport import EncryptedFolderSyncTransport, FolderSyncTransport

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731
_SYNC_CREDENTIAL_ID = "sync:folder-transport"
_SYNC_CREDENTIAL_PROVIDER = "haven.sync"

_CONFLICT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_conflicts (
    conflict_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    local_revision INTEGER NOT NULL,
    remote_revision INTEGER NOT NULL,
    local_payload TEXT NOT NULL,
    remote_payload TEXT NOT NULL,
    causal_parents TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolution TEXT,
    resolution_justification TEXT
);
"""


class LocalSyncEngine:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        outbox: SyncEventStore | None = None,
        clock=_DEFAULT_CLOCK,
        allowed_local_scopes: tuple[str, ...] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._outbox = outbox or SyncEventStore(self._data_dir / "sync_events.db")
        self._clock = clock
        self._device_id = device_id_for(self._data_dir)
        self._state_path = self._data_dir / "sync_state.json"
        self._state = self._load_state()
        self._sync_key = self._load_sync_key()
        self._enabled = bool(self._state.get("enabled", False)) and self._sync_key is not None
        if bool(self._state.get("enabled", False)) and self._sync_key is None:
            # A pre-authentication state file must never reactivate sync after
            # restart. It remains inspectable, but the live engine is off
            # until the owner configures a protected shared key.
            self._state["enabled"] = False
            self._save_state()
        self._transport: Any = None
        if self._state.get("transport"):
            transport_state = self._state["transport"]
            if transport_state.get("kind", "folder") == "encrypted_folder":
                if self._sync_key is not None:
                    try:
                        self._transport = EncryptedFolderSyncTransport(
                            export_dir=transport_state["export_dir"],
                            import_dir=transport_state["import_dir"],
                            key=self._sync_key,
                        )
                    except (KeyError, RuntimeError, ValueError):
                        # A missing optional crypto runtime must not make the
                        # installation unbootable; the transport stays absent
                        # and the owner receives a truthful status/error.
                        self._transport = None
            else:
                self._transport = FolderSyncTransport(
                    export_dir=transport_state["export_dir"],
                    import_dir=transport_state["import_dir"],
                )
        self._appliers: dict[str, Callable[[dict[str, Any], SyncEvent], dict]] = {}
        # Explicit foreign->local scope aliases (a same-user device pair maps
        # the origin's personal scope onto this installation's). Without an
        # alias an invisible scope stays rejected: fail closed by default.
        self._allowed_local_scopes = frozenset(allowed_local_scopes or ())
        raw_aliases = self._state.get("scope_aliases", {})
        self._scope_aliases = (
            {
                foreign: local
                for foreign, local in raw_aliases.items()
                if isinstance(foreign, str)
                and foreign.strip()
                and isinstance(local, str)
                and local.strip()
                and (not self._allowed_local_scopes or local in self._allowed_local_scopes)
            }
            if isinstance(raw_aliases, dict)
            else {}
        )
        self._lock = threading.Lock()
        self._conflicts_path = self._data_dir / "sync_conflicts.db"
        conn = sqlite3.connect(str(self._conflicts_path))
        try:
            conn.executescript(_CONFLICT_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_conflicts)").fetchall()}
            if "resolution_justification" not in columns:
                conn.execute("ALTER TABLE sync_conflicts ADD COLUMN resolution_justification TEXT")
            conn.commit()
        finally:
            conn.close()

    # -- configuration ------------------------------------------------------------

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> dict:
        if bool(enabled) and self._sync_key is None:
            return {"ok": False, "error": "sync requires a protected shared transport key"}
        self._enabled = bool(enabled)
        self._state["enabled"] = self._enabled
        self._save_state()
        return {"ok": True, "enabled": self._enabled}

    def set_transport(self, transport, *, auth_key: str | None = None) -> dict:
        """Configure a folder transport while keeping the seam transport-agnostic."""

        if auth_key is not None:
            if not isinstance(auth_key, str) or not auth_key.strip():
                return {"ok": False, "error": "auth_key must be a non-empty string"}
            new_key = auth_key.strip().encode("utf-8")
            if self._sync_key != new_key and self._outbox.since(self._state["outbound_seq"]):
                return {
                    "ok": False,
                    "error": "cannot rotate a key while outbound events are pending",
                }
            try:
                credentials = self._credentials()
                existing = next(
                    iter(credentials.list_metadata(provider=_SYNC_CREDENTIAL_PROVIDER)), None
                )
                if existing is None:
                    credentials.create(
                        credential_id=_SYNC_CREDENTIAL_ID,
                        provider=_SYNC_CREDENTIAL_PROVIDER,
                        account_label=self._device_id,
                        kind=CredentialKind.DEVICE,
                        secret=auth_key.strip(),
                    )
                else:
                    credentials.rotate(_SYNC_CREDENTIAL_ID, new_secret=auth_key.strip())
            except (OSError, RuntimeError, ValueError, UnknownCredentialError) as exc:
                return {"ok": False, "error": f"could not protect sync key: {exc}"}
            self._sync_key = new_key

        self._transport = transport
        self._state["transport"] = (
            {
                "kind": getattr(transport, "kind", "folder"),
                "export_dir": str(transport.export_dir),
                "import_dir": str(transport.import_dir),
            }
            if transport is not None and hasattr(transport, "export_dir")
            else None
        )
        self._save_state()
        return {
            "ok": True,
            "transport": type(transport).__name__ if transport else None,
            "authenticated": self._sync_key is not None,
        }

    def set_scope_aliases(
        self,
        aliases: dict[str, str],
        *,
        allowed_local_scopes: tuple[str, ...] | None = None,
    ) -> dict:
        """Map foreign scope ids onto local ones for applied records."""

        if not isinstance(aliases, dict):
            return {"ok": False, "error": "aliases must be a mapping"}
        cleaned = {}
        allowed = self._allowed_local_scopes or frozenset(allowed_local_scopes or ())
        for foreign, local in aliases.items():
            if (
                not isinstance(foreign, str)
                or not foreign.strip()
                or not isinstance(local, str)
                or not local.strip()
            ):
                return {"ok": False, "error": "alias keys and values must be strings"}
            if allowed and local.strip() not in allowed:
                return {"ok": False, "error": f"local scope is not visible: {local}"}
            cleaned[foreign.strip()] = local.strip()
        self._scope_aliases = cleaned
        self._state["scope_aliases"] = dict(cleaned)
        self._save_state()
        return {"ok": True, "aliases": dict(cleaned)}

    def _aliased_scope(self, scope_id: str) -> str:
        return self._scope_aliases.get(scope_id, scope_id)

    def register_applier(self, kind: str, applier: Callable[[dict[str, Any], SyncEvent], dict]) -> None:
        """The apply seam: kind -> (payload_dict, event) -> result envelope."""

        self._appliers[kind] = applier

    def _load_state(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        state = dict(data)
        state["outbound_seq"] = int(data.get("outbound_seq", 0))
        state["inbound_seq"] = int(data.get("inbound_seq", 0))
        return state

    def _credentials(self) -> CredentialStore:
        return CredentialStore(self._data_dir / "credentials.db", clock=self._clock)

    def _load_sync_key(self) -> bytes | None:
        try:
            secret = self._credentials().get_secret(_SYNC_CREDENTIAL_ID)
        except (UnknownCredentialError, OSError, RuntimeError, ValueError):
            return None
        return secret.encode("utf-8") if secret else None

    def _save_state(self) -> None:
        self._state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    # -- producer seam --------------------------------------------------------------

    def record_mutation(
        self,
        *,
        object_id: str,
        kind: str,
        scope_id: str,
        revision: int,
        causal_parents: tuple[str, ...] = (),
        payload: dict[str, Any],
    ) -> dict:
        """Append one mutation to the outbox. Excluded kinds never enter."""

        if not is_syncable(kind):
            return {"ok": False, "error": f"{kind!r} is not in the sync allow-set"}
        if self._allowed_local_scopes and scope_id not in self._allowed_local_scopes:
            return {"ok": False, "error": f"scope is not visible: {scope_id}"}
        if not self._enabled:
            return {"ok": True, "recorded": False, "reason": "sync is disabled"}
        latest = self._outbox.latest_for(object_id)
        event = SyncEvent(
            event_id=f"evt-{uuid4()}",
            seq=self._outbox.next_seq(),
            origin_device_id=self._device_id,
            object_id=object_id,
            kind=kind,
            scope_id=scope_id,
            revision=revision,
            causal_parents=tuple(causal_parents) or ((latest.event_id,) if latest else ()),
            payload=tuple(sorted(payload.items())),
            occurred_at=self._clock(),
        ).signed(self._sync_key)
        self._outbox.append(event)
        return {"ok": True, "recorded": True, "event_id": event.event_id, "seq": event.seq}

    # -- push / pull ------------------------------------------------------------------

    def push(self) -> dict:
        if not self._enabled:
            return {"ok": False, "error": "sync is disabled"}
        if self._transport is None:
            return {"ok": False, "error": "no sync transport is configured"}
        events = self._outbox.since(self._state["outbound_seq"])
        if any(not event.verify(self._sync_key) for event in events):
            return {"ok": False, "error": "outbox contains unsigned or invalid sync events"}
        sent = self._transport.send(events)
        if sent:
            self._state["outbound_seq"] = events[sent - 1].seq
            self._save_state()
        return {"ok": True, "pushed": sent, "cursor": str(self._state["outbound_seq"])}

    def pull(self) -> dict:
        if not self._enabled:
            return {"ok": False, "error": "sync is disabled"}
        if self._transport is None:
            return {"ok": False, "error": "no sync transport is configured"}
        events, _watermark = self._transport.fetch(since_seq=self._state["inbound_seq"])
        applied = rejected = skipped = 0
        conflicts: list[str] = []
        trusted_watermark = self._state["inbound_seq"]
        for event in events:
            if not event.verify(self._sync_key):
                rejected += 1
                continue
            # Only authenticated records may advance the cursor. Otherwise
            # a forged high sequence number could permanently skip legitimate
            # lower-sequence events from the peer (a denial-of-service at the
            # same boundary we use to reject the forged mutation).
            trusted_watermark = max(trusted_watermark, event.seq)
            if event.origin_device_id == self._device_id:
                skipped += 1  # our own event echoed back
                continue
            outcome = self._apply(event)
            applied += outcome.get("applied", 0)
            rejected += outcome.get("rejected", 0)
            skipped += outcome.get("skipped", 0)
            if outcome.get("conflict_id"):
                conflicts.append(outcome["conflict_id"])
        self._state["inbound_seq"] = trusted_watermark
        self._save_state()
        return {
            "ok": True,
            "pulled": len(events),
            "applied": applied,
            "rejected": rejected,
            "skipped": skipped,
            "conflicts": conflicts,
            "cursor": str(trusted_watermark),
        }

    def _apply(self, event: SyncEvent) -> dict:
        if not is_syncable(event.kind):
            # Fail closed: a foreign record of an excluded kind never lands.
            return {"rejected": 1}
        applier = self._appliers.get(event.kind)
        if applier is None:
            return {"rejected": 1}
        payload = dict(event.payload)
        raw_scope = payload.get("scope_id", event.scope_id)
        if not isinstance(raw_scope, str) or raw_scope.strip() != event.scope_id:
            return {"rejected": 1}
        payload["scope_id"] = self._aliased_scope(raw_scope.strip())
        if self._allowed_local_scopes and payload["scope_id"] not in self._allowed_local_scopes:
            return {"rejected": 1}
        local = self._outbox.latest_for(event.object_id)
        local_payload = dict(local.payload) if local is not None else None
        if (
            local is not None
            and local.revision >= event.revision
            and local_payload != payload
        ):
            # Divergent histories: durable conflict, never silent LWW. One
            # open conflict per object -- later divergent events fold into
            # the existing review instead of stacking duplicates.
            if self._has_open_conflict(event.object_id):
                return {"skipped": 1}
            conflict_id = self._raise_conflict(event, local, payload)
            return {"conflict_id": conflict_id}
        if local is not None and local.revision == event.revision and local_payload == payload:
            return {"skipped": 1}
        result = applier(payload, event)
        if not result.get("ok"):
            return {"rejected": 1}
        # Record the application in our own outbox (with correct lineage) so
        # the object converges and third devices see the causal chain. The
        # echo keeps the ORIGINAL origin device: it is the same event, and a
        # device must recognize its own events echoed back (skip, not
        # conflict).
        self._outbox.append(
            SyncEvent(
                event_id=f"evt-{uuid4()}",
                seq=self._outbox.next_seq(),
                origin_device_id=event.origin_device_id,
                object_id=event.object_id,
                kind=event.kind,
                scope_id=event.scope_id,
                revision=event.revision,
                causal_parents=(event.event_id,),
                payload=event.payload,
                occurred_at=self._clock(),
            ).signed(self._sync_key)
        )
        return {"applied": 1}

    # -- conflicts -----------------------------------------------------------------------

    def _has_open_conflict(self, object_id: str) -> bool:
        conn = sqlite3.connect(str(self._conflicts_path))
        try:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM sync_conflicts WHERE object_id = ? AND resolved = 0",
                (object_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(count) > 0

    def _raise_conflict(self, event: SyncEvent, local: SyncEvent, payload: dict) -> str:
        conflict_id = f"conflict-{uuid4()}"
        conn = sqlite3.connect(str(self._conflicts_path))
        try:
            conn.execute(
                "INSERT INTO sync_conflicts(conflict_id, object_id, kind, scope_id, "
                "local_revision, remote_revision, local_payload, remote_payload, "
                "causal_parents, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conflict_id,
                    event.object_id,
                    event.kind,
                    event.scope_id,
                    local.revision,
                    event.revision,
                    json.dumps(dict(local.payload)),
                    json.dumps(payload),
                    json.dumps([local.event_id, event.event_id]),
                    self._clock().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return conflict_id

    def conflicts(self) -> dict:
        conn = sqlite3.connect(str(self._conflicts_path))
        try:
            rows = conn.execute(
                "SELECT conflict_id, object_id, kind, scope_id, local_revision, remote_revision, "
                "local_payload, remote_payload, occurred_at, resolved, resolution "
                "FROM sync_conflicts WHERE resolved = 0 ORDER BY occurred_at"
            ).fetchall()
        finally:
            conn.close()
        return {
            "ok": True,
            "conflicts": [
                {
                    "conflict_id": row[0],
                    "object_id": row[1],
                    "kind": row[2],
                    "scope_id": row[3],
                    "local_revision": row[4],
                    "remote_revision": row[5],
                    "local_payload": json.loads(row[6]),
                    "remote_payload": json.loads(row[7]),
                    "occurred_at": row[8],
                }
                for row in rows
            ],
        }

    def resolve(
        self,
        *,
        conflict_id: str | None,
        choice: str | None,
        merge_fields=None,
        justification: str | None,
    ) -> dict:
        """Choose A (local), B (remote), or merge fields where legal."""

        if not isinstance(conflict_id, str) or not conflict_id.strip():
            return {"ok": False, "error": "a non-empty 'conflict_id' is required"}
        if choice not in ("local", "remote", "merge"):
            return {"ok": False, "error": "choice must be 'local', 'remote', or 'merge'"}
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "conflict resolution requires a non-empty justification"}
        conn = sqlite3.connect(str(self._conflicts_path))
        try:
            row = conn.execute(
                "SELECT object_id, kind, scope_id, local_revision, remote_revision, "
                "local_payload, remote_payload FROM sync_conflicts "
                "WHERE conflict_id = ? AND resolved = 0",
                (conflict_id.strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"ok": False, "error": f"unknown or resolved conflict: {conflict_id}"}
        object_id, kind, scope_id, local_rev, remote_rev, local_raw, remote_raw = row
        local_payload = json.loads(local_raw)
        remote_payload = json.loads(remote_raw)
        applier = self._appliers.get(kind)
        if applier is None:
            return {"ok": False, "error": f"no applier registered for {kind!r}"}

        if choice == "local":
            chosen, revision, source = dict(local_payload), local_rev, "local"
        elif choice == "remote":
            chosen, revision, source = dict(remote_payload), remote_rev, "remote"
        else:
            if not isinstance(merge_fields, dict) or not merge_fields:
                return {"ok": False, "error": "merge requires a non-empty 'merge_fields' object"}
            allowed = set(local_payload) | set(remote_payload)
            unknown = set(merge_fields) - allowed
            if unknown:
                return {"ok": False, "error": f"merge fields are not part of the record: {sorted(unknown)}"}
            chosen = {**remote_payload, **local_payload, **merge_fields}
            revision = max(local_rev, remote_rev) + 1
            chosen["revision"] = revision
            source = "merge"

        chosen_scope = chosen.get("scope_id", scope_id)
        expected_local_scope = self._aliased_scope(scope_id)
        if (
            not isinstance(chosen_scope, str)
            or chosen_scope.strip() not in {scope_id, expected_local_scope}
            or (
                self._allowed_local_scopes
                and self._aliased_scope(chosen_scope.strip()) not in self._allowed_local_scopes
            )
        ):
            return {"ok": False, "error": "conflict resolution scope is not visible"}

        result = applier(chosen, None)
        if not result.get("ok"):
            return result
        self._outbox.append(
            SyncEvent(
                event_id=f"evt-{uuid4()}",
                seq=self._outbox.next_seq(),
                origin_device_id=self._device_id,
                object_id=object_id,
                kind=kind,
                scope_id=scope_id,
                revision=revision,
                causal_parents=(),
                payload=tuple(sorted(chosen.items())),
                occurred_at=self._clock(),
            ).signed(self._sync_key)
        )
        conn = sqlite3.connect(str(self._conflicts_path))
        try:
            conn.execute(
                "UPDATE sync_conflicts SET resolved = 1, resolution = ?, "
                "resolution_justification = ? WHERE conflict_id = ?",
                (source, justification.strip(), conflict_id.strip()),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "resolved": conflict_id.strip(), "choice": source, "applied": chosen}

    def status(self) -> dict:
        return {
            "ok": True,
            "enabled": self._enabled,
            "device_id": self._device_id,
            "transport": type(self._transport).__name__ if self._transport is not None else None,
            "authenticated": self._sync_key is not None,
            "outbound_seq": self._state["outbound_seq"],
            "inbound_seq": self._state["inbound_seq"],
        }


__all__ = ["LocalSyncEngine"]
