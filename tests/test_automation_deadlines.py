"""Generic deadline observations remain bounded and evidence-aware."""

from datetime import datetime, timezone

import pytest

from haven.automation import AutomationDeadline
from haven.core.domain import EvidenceStatus


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_deadline_projection_keeps_reserved_identity_and_payload_separate():
    deadline = AutomationDeadline(
        deadline_id="task:1",
        household_id="household-a",
        source_kind="task",
        source_id="task-1",
        due_at=NOW,
        payload={"title": "Prepare notes", "priority": "high"},
    )

    projected = deadline.as_dict()
    assert projected["deadline_id"] == "task:1"
    assert projected["due_at"] == NOW
    assert projected["title"] == "Prepare notes"
    assert deadline.fire_at(-15) == datetime(2026, 10, 1, 11, 45, tzinfo=timezone.utc)


def test_deadline_rejects_naive_or_reserved_payload():
    with pytest.raises(ValueError, match="timezone-aware"):
        AutomationDeadline(
            deadline_id="task:1",
            household_id="household-a",
            source_kind="task",
            source_id="task-1",
            due_at=datetime(2026, 10, 1, 12, 0),
        )
    with pytest.raises(ValueError, match="reserved"):
        AutomationDeadline(
            deadline_id="task:1",
            household_id="household-a",
            source_kind="task",
            source_id="task-1",
            due_at=NOW,
            payload={"household_id": "other"},
        )


def test_degraded_deadline_is_not_eligible():
    for status in (EvidenceStatus.STALE, EvidenceStatus.FALLBACK, EvidenceStatus.UNAVAILABLE):
        deadline = AutomationDeadline(
            deadline_id=f"task:{status.value}",
            household_id="household-a",
            source_kind="task",
            source_id="task-1",
            due_at=NOW,
            evidence_status=status,
        )
        assert deadline.is_eligible_for_automation is False
