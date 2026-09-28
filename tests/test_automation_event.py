"""AutomationEvent: the shared vocabulary a real event emitter and
`ResourceActionScheduler.handle_events` agree on (see `haven/automation/
events.py`'s module docstring for what is and is not built yet)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from haven.automation import AutomationEvent

UTC = timezone.utc
NOW = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)


def _event(**overrides) -> AutomationEvent:
    kwargs = dict(
        event_id="event-1",
        event_name="file.created",
        household_id="household-a",
        occurred_at=NOW,
        payload={"resource_id": "file-1"},
    )
    kwargs.update(overrides)
    return AutomationEvent(**kwargs)


def test_constructs_with_a_mapping_payload():
    event = _event()
    assert event.as_dict() == {"resource_id": "file-1"}


def test_accepts_a_tuple_payload_identically():
    from_tuple = _event(payload=(("resource_id", "file-1"),))
    assert from_tuple.as_dict() == {"resource_id": "file-1"}


def test_defaults_to_an_empty_payload():
    event = AutomationEvent(event_id="e1", event_name="task.status_changed", household_id="household-a", occurred_at=NOW)
    assert event.as_dict() == {}


@pytest.mark.parametrize("field_name", ["event_id", "event_name", "household_id"])
def test_rejects_blank_identity_fields(field_name):
    with pytest.raises(ValueError):
        _event(**{field_name: ""})


def test_requires_an_aware_datetime():
    with pytest.raises(ValueError):
        _event(occurred_at=datetime(2026, 9, 27, 21, 0))  # naive
