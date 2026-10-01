"""Production-domain adapters publish trusted, bounded automation events."""

from datetime import datetime, timezone

from haven.automation import (
    AutomationEventFeed,
    ComputerResourceAutomationEmitter,
    EmailAutomationEmitter,
    ProviderHealthAutomationEmitter,
    TaskAutomationEmitter,
)
from haven.core.domain import EvidenceStatus
from haven.domains.tasks.models import OPEN, TaskRecord
from haven.integrations.comms.email import EmailMessage
from haven.resources.models import ResourceRecord


NOW = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def _feed():
    return AutomationEventFeed(source="tests", household_id="household-a", clock=lambda: NOW)


def _task(*, state=OPEN, revision=0):
    return TaskRecord(
        task_id="task:1",
        scope_id="scope:1",
        title="Review the file",
        detail="",
        state=state,
        created_at=NOW,
        updated_at=NOW,
        created_by="person:1",
        revision=revision,
    )


def _resource(resource_id="file:1"):
    return ResourceRecord(
        resource_id=resource_id,
        resource_type="file",
        scope_id="scope:1",
        provider_id="haven.computer.filesystem",
        title="report.txt",
        locator="C:/allowed/report.txt",
        capabilities=(),
        observed_at=NOW,
    )


def test_task_emitter_only_publishes_creation_and_status_boundaries():
    feed = _feed()
    emitter = TaskAutomationEmitter(feed)

    created = emitter.changed(None, _task())
    assert created is not None
    assert created.event_name == "task.created"
    assert created.as_dict()["to_state"] == OPEN
    assert created.is_eligible_for_automation

    assert emitter.changed(_task(), _task()) is None
    changed = emitter.changed(_task(), _task(state="done", revision=1))
    assert changed is not None
    assert changed.event_name == "task.status_changed"
    assert changed.as_dict()["from_state"] == OPEN
    assert changed.as_dict()["to_state"] == "done"


def test_file_and_email_emitters_only_publish_first_seen_items():
    feed = _feed()
    files = ComputerResourceAutomationEmitter(feed)
    file_events = files.new_resources([_resource("file:1"), _resource("file:2")], is_new=lambda value: value == "file:2")
    assert [event.event_name for event in file_events] == ["file.created"]
    assert file_events[0].as_dict()["resource_id"] == "file:2"

    mail = EmailAutomationEmitter(feed)
    message = EmailMessage(
        message_id="<message-1>",
        sender="sender@example.test",
        subject="A bounded subject",
        at="today",
        thread_id="<thread-1>",
        snippet="this must not be copied into the event payload",
    )
    mail_events = mail.new_messages([message], is_new=lambda value: value == "message-1", occurred_at=NOW)
    assert len(mail_events) == 1
    assert mail_events[0].event_name == "message.received"
    assert "snippet" not in mail_events[0].as_dict()


def test_provider_health_emitter_only_publishes_transitions():
    feed = _feed()
    emitter = ProviderHealthAutomationEmitter(feed)
    first = emitter.changed(provider_id="home_assistant", reachable=True, detail="1 state", occurred_at=NOW)
    assert first is not None
    assert first.as_dict()["to_state"] == "reachable"
    assert emitter.changed(provider_id="home_assistant", reachable=True, detail="2 states", occurred_at=NOW) is None
    second = emitter.changed(provider_id="home_assistant", reachable=False, detail="timeout", occurred_at=NOW)
    assert second is not None
    assert second.as_dict()["from_state"] == "reachable"
    assert second.as_dict()["to_state"] == "unavailable"


def test_feed_is_bounded_and_failing_consumers_do_not_break_publish():
    feed = AutomationEventFeed(source="tests", household_id="household-a", clock=lambda: NOW, max_events=2)
    received = []
    feed.subscribe(received.append)
    feed.subscribe(lambda _event: (_ for _ in ()).throw(RuntimeError("consumer failed")))
    emitter = ProviderHealthAutomationEmitter(feed)
    for provider_id in ("one", "two", "three"):
        emitter.changed(provider_id=provider_id, reachable=True, detail="ok", occurred_at=NOW)
    assert len(received) == 3
    assert len(feed.recent()) == 2
    assert all(event.evidence_status is EvidenceStatus.OBSERVED for event in feed.recent())
