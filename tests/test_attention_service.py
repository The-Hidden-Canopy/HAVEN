"""NeedsYouService: aggregation, dedup, ranking, snooze/dismiss lifecycle
(spec sections 5 and 7). Uses fake sources so ranking/dedup/persistence are
tested in isolation from any real domain store."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from haven.attention.domain import (
    AttentionActionRef,
    AttentionItem,
    AttentionKind,
    AttentionSeverity,
    Dismissibility,
    RouteRef,
)
from haven.attention.service import NeedsYouService

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SCOPE = "scope:personal-1"


class _FakeIdentity:
    def visible_scope_ids(self):
        return (SCOPE,)


class _FakeSource:
    def __init__(self, items):
        self._items = items

    def collect(self, *, now, visible_scope_ids):
        return list(self._items)


def _item(
    ref,
    *,
    kind=AttentionKind.BLOCKER,
    severity=AttentionSeverity.NORMAL,
    dismissibility=Dismissibility.SNOOZE,
    created_at=NOW,
    due_at=None,
):
    return AttentionItem(
        attention_id=f"attn:{ref}",
        kind=kind,
        severity=severity,
        title=ref,
        summary="summary",
        why_now="why now",
        source_domain="tasks",
        source_ref=ref,
        scope_id=SCOPE,
        created_at=created_at,
        due_at=due_at,
        route=RouteRef(page="tasks", entity_id=ref),
        available_actions=(AttentionActionRef(action="route", label="Open"),),
        dismissibility=dismissibility,
    )


def _service(sources, tmp_path, *, clock=lambda: NOW):
    return NeedsYouService(
        sources=sources,
        identity=_FakeIdentity(),
        state_path=Path(tmp_path) / "needs_you.json",
        clock=clock,
    )


def test_no_signals_no_fabricated_items(tmp_path):
    service = _service([_FakeSource([])], tmp_path)
    result = service.list_open()
    assert result == {"ok": True, "count": 0, "items": [], "generated_at": NOW.isoformat()}


def test_deduplicates_by_source_ref_across_adapters():
    with tempfile.TemporaryDirectory() as tmp:
        same_ref_a = _item("shared-ref")
        same_ref_b = _item("shared-ref")
        service = _service([_FakeSource([same_ref_a]), _FakeSource([same_ref_b])], tmp)
        result = service.list_open()
        assert result["count"] == 1


def test_ranking_orders_by_tier_then_due_time():
    with tempfile.TemporaryDirectory() as tmp:
        authority = _item("authority-item", kind=AttentionKind.AUTHORITY, dismissibility=Dismissibility.NONE)
        conflict = _item("conflict-item", kind=AttentionKind.CONFLICT, dismissibility=Dismissibility.DISMISS)
        early_blocker = _item("early-blocker", kind=AttentionKind.BLOCKER, due_at=NOW + timedelta(hours=1))
        late_blocker = _item("late-blocker", kind=AttentionKind.BLOCKER, due_at=NOW + timedelta(hours=5))
        service = _service([_FakeSource([conflict, late_blocker, authority, early_blocker])], tmp)

        refs = [item["source_ref"] for item in service.list_open()["items"]]
        assert refs == ["authority-item", "early-blocker", "late-blocker", "conflict-item"]


def test_critical_severity_overrides_kind_tier():
    with tempfile.TemporaryDirectory() as tmp:
        authority = _item("authority-item", kind=AttentionKind.AUTHORITY, dismissibility=Dismissibility.NONE)
        critical_conflict = _item(
            "critical-conflict",
            kind=AttentionKind.CONFLICT,
            severity=AttentionSeverity.CRITICAL,
            dismissibility=Dismissibility.DISMISS,
        )
        service = _service([_FakeSource([authority, critical_conflict])], tmp)
        refs = [item["source_ref"] for item in service.list_open()["items"]]
        assert refs[0] == "critical-conflict"


def test_authority_items_cannot_be_snoozed_or_dismissed():
    with tempfile.TemporaryDirectory() as tmp:
        authority = _item("authority-item", kind=AttentionKind.AUTHORITY, dismissibility=Dismissibility.NONE)
        service = _service([_FakeSource([authority])], tmp)

        assert service.snooze(source_ref="authority-item")["ok"] is False
        assert service.dismiss(source_ref="authority-item")["ok"] is False
        assert service.list_open()["count"] == 1


def test_dismiss_removes_item_and_persists_across_reload():
    with tempfile.TemporaryDirectory() as tmp:
        conflict = _item("conflict-item", kind=AttentionKind.CONFLICT, dismissibility=Dismissibility.DISMISS)
        source = _FakeSource([conflict])
        service = _service([source], tmp)

        result = service.dismiss(source_ref="conflict-item")
        assert result["ok"] is True
        assert service.list_open()["count"] == 0

        reloaded = _service([source], tmp)
        assert reloaded.list_open()["count"] == 0


def test_dismiss_rejects_non_dismissible_kind():
    with tempfile.TemporaryDirectory() as tmp:
        blocker = _item("blocker-item", kind=AttentionKind.BLOCKER, dismissibility=Dismissibility.SNOOZE)
        service = _service([_FakeSource([blocker])], tmp)
        result = service.dismiss(source_ref="blocker-item")
        assert result["ok"] is False


def test_snoozed_item_hides_then_wakes_when_condition_persists():
    with tempfile.TemporaryDirectory() as tmp:
        clock = {"now": NOW}
        blocker = _item("blocker-item", kind=AttentionKind.BLOCKER, dismissibility=Dismissibility.SNOOZE)
        source = _FakeSource([blocker])
        service = _service([source], tmp, clock=lambda: clock["now"])

        assert service.snooze(source_ref="blocker-item")["ok"] is True
        assert service.list_open()["count"] == 0

        clock["now"] = NOW + timedelta(hours=5)
        result = service.list_open()
        assert result["count"] == 1
        assert result["items"][0]["source_ref"] == "blocker-item"


def test_snoozed_item_stays_gone_once_source_condition_clears():
    with tempfile.TemporaryDirectory() as tmp:
        clock = {"now": NOW}
        mutable_source = {"items": [_item("blocker-item", kind=AttentionKind.BLOCKER)]}

        class _MutableSource:
            def collect(self, *, now, visible_scope_ids):
                return list(mutable_source["items"])

        service = _service([_MutableSource()], tmp, clock=lambda: clock["now"])
        assert service.snooze(source_ref="blocker-item")["ok"] is True

        mutable_source["items"] = []  # the underlying blocker resolved
        clock["now"] = NOW + timedelta(hours=5)
        assert service.list_open()["count"] == 0


def test_unknown_source_ref_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        service = _service([_FakeSource([])], tmp)
        assert service.snooze(source_ref="nope")["ok"] is False
        assert service.dismiss(source_ref="nope")["ok"] is False
