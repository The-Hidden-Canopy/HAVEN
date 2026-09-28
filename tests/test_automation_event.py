"""AutomationEvent: the shared vocabulary a real event emitter and
`ResourceActionScheduler.handle_events` agree on (see `haven/automation/
events.py`'s module docstring for what is and is not built yet)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from haven.automation import AutomationEvent, AutomationEventPublisher
from haven.core.domain import EvidenceStatus

UTC = timezone.utc
NOW = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)


def _event(**overrides) -> AutomationEvent:
    kwargs = dict(
        event_id="event-1",
        event_name="file.created",
        household_id="household-a",
        occurred_at=NOW,
        source="tests.automation_event",
        payload={"resource_id": "file-1"},
    )
    kwargs.update(overrides)
    return AutomationEvent(**kwargs)


def test_constructs_with_a_mapping_payload():
    event = _event()
    assert event.as_dict() == {"resource_id": "file-1"}


def test_payload_is_snapshotted_and_as_dict_does_not_expose_nested_aliases():
    nested = {"attempts": [1]}
    event = _event(payload={"details": nested})

    nested["attempts"].append(2)
    assert event.as_dict() == {"details": {"attempts": [1]}}

    snapshot = event.as_dict()
    snapshot["details"]["attempts"].append(3)
    assert event.as_dict() == {"details": {"attempts": [1]}}


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


def test_raw_events_are_not_trusted_until_published_by_a_scoped_source():
    raw = _event()
    published = AutomationEventPublisher(
        source="tests.automation_event",
        household_id="household-a",
    ).publish(
        event_id="event-2",
        event_name="file.created",
        occurred_at=NOW,
        payload={"resource_id": "file-1"},
    )

    assert raw.is_trusted is False
    assert raw.is_eligible_for_automation is False
    assert published.is_trusted is True
    assert published.is_eligible_for_automation is True


def test_publisher_binds_household_and_degraded_evidence_is_ineligible():
    publisher = AutomationEventPublisher(source="provider.files", household_id="household-a")
    stale = publisher.publish(
        event_id="event-stale",
        event_name="file.created",
        occurred_at=NOW,
        evidence_status=EvidenceStatus.STALE,
    )
    fallback = publisher.publish(
        event_id="event-fallback",
        event_name="file.created",
        occurred_at=NOW,
        evidence_status=EvidenceStatus.FALLBACK,
    )

    assert stale.household_id == "household-a"
    assert stale.is_eligible_for_automation is False
    assert fallback.is_eligible_for_automation is False


def test_publisher_rejects_blank_source_or_household():
    with pytest.raises(ValueError):
        AutomationEventPublisher(source="", household_id="household-a")
    with pytest.raises(ValueError):
        AutomationEventPublisher(source="provider.files", household_id="")
