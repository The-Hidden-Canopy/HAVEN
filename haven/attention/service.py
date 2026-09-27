"""NeedsYouService: aggregate, deduplicate, rank, snooze/dismiss (spec 5, 7).

The service never mutates a domain (spec 4.1) -- it only reads through the
source adapters it is given and manages its own open/snoozed/dismissed
bookkeeping, the same restart-durable-dismissal idiom `TodayService`
already uses for suggestion cards.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .domain import AttentionItem, Dismissibility, default_dismissibility

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

# How long a snoozed item stays hidden before it wakes back up (spec 5:
# "wake/due" -- the first version uses a fixed window rather than trying to
# infer a per-item wake time from evidence the source adapters don't supply).
_SNOOZE_DURATION = timedelta(hours=4)


class AttentionSource(Protocol):
    def collect(self, *, now: datetime, **kwargs) -> list[AttentionItem]: ...


class NeedsYouService:
    def __init__(
        self,
        *,
        sources: list,
        identity,
        state_path: str | Path,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        self._sources = sources
        self._identity = identity
        self._state_path = Path(state_path)
        self._clock = clock
        self._lock = threading.Lock()
        self._snoozed_until: dict[str, datetime] = {}
        self._dismissed: set[str] = set()
        self._load_state()

    # -- persistence ---------------------------------------------------------

    def _load_state(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self._dismissed = {str(item) for item in data.get("dismissed", [])}
        for source_ref, iso in data.get("snoozed_until", {}).items():
            try:
                self._snoozed_until[str(source_ref)] = datetime.fromisoformat(iso)
            except ValueError:
                continue

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(
                {
                    "dismissed": sorted(self._dismissed),
                    "snoozed_until": {
                        ref: until.isoformat() for ref, until in self._snoozed_until.items()
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- the projection --------------------------------------------------------

    def _collect_raw(self, now: datetime) -> list[AttentionItem]:
        visible = self._identity.visible_scope_ids()
        raw: list[AttentionItem] = []
        for source in self._sources:
            try:
                raw.extend(source.collect(now=now, visible_scope_ids=visible))
            except Exception:
                continue
        return raw

    def _deduplicate(self, items: list[AttentionItem]) -> list[AttentionItem]:
        """Collapse candidates that share a `source_ref` (spec 3.4) --
        multiple adapters pointing at the same underlying condition must
        render once, not once per adapter."""

        seen: dict[str, AttentionItem] = {}
        for item in items:
            if item.source_ref not in seen:
                seen[item.source_ref] = item
        return list(seen.values())

    def list_open(self) -> dict:
        now = self._clock()
        raw = self._deduplicate(self._collect_raw(now))
        open_items = []
        for item in raw:
            if item.source_ref in self._dismissed:
                continue
            snoozed_until = self._snoozed_until.get(item.source_ref)
            if snoozed_until is not None:
                if now < snoozed_until:
                    continue
                # The snooze window passed and the source condition is still
                # present -- it wakes back up (spec 5's lifecycle diagram).
                del self._snoozed_until[item.source_ref]
            open_items.append(item)
        open_items.sort(
            key=lambda item: (
                item.tier,
                item.due_at or item.expires_at or item.created_at,
                item.created_at,
                item.attention_id,
            )
        )
        return {
            "ok": True,
            "count": len(open_items),
            "items": [item.to_dict() for item in open_items],
            "generated_at": now.isoformat(),
        }

    # -- lifecycle actions (spec 5.1) -------------------------------------------

    def _find_open(self, source_ref: str) -> AttentionItem | None:
        now = self._clock()
        for item in self._deduplicate(self._collect_raw(now)):
            if item.source_ref == source_ref:
                return item
        return None

    def snooze(self, *, source_ref: str | None) -> dict:
        if not isinstance(source_ref, str) or not source_ref.strip():
            return {"ok": False, "error": "a non-empty 'source_ref' is required"}
        source_ref = source_ref.strip()
        item = self._find_open(source_ref)
        if item is None:
            return {"ok": False, "error": "unknown or already-resolved attention item"}
        if item.dismissibility == Dismissibility.NONE:
            return {"ok": False, "error": "authority items cannot be snoozed"}
        with self._lock:
            self._snoozed_until[source_ref] = self._clock() + _SNOOZE_DURATION
            self._save_state()
        return {"ok": True, "snoozed_until": self._snoozed_until[source_ref].isoformat()}

    def dismiss(self, *, source_ref: str | None) -> dict:
        if not isinstance(source_ref, str) or not source_ref.strip():
            return {"ok": False, "error": "a non-empty 'source_ref' is required"}
        source_ref = source_ref.strip()
        item = self._find_open(source_ref)
        if item is None:
            return {"ok": False, "error": "unknown or already-resolved attention item"}
        if item.dismissibility != Dismissibility.DISMISS:
            return {"ok": False, "error": "this item cannot be dismissed"}
        with self._lock:
            self._dismissed.add(source_ref)
            self._save_state()
        return {"ok": True, "dismissed": source_ref}


__all__ = ["NeedsYouService"]
