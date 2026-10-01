"""Durable resource-automation rules, events, and scheduler memory."""

from datetime import datetime, timezone

from haven.automation import (
    ActionTarget,
    AutomationDeadline,
    AutomationEventFeed,
    AutomationSpec,
    ResourceAutomationService,
    Selector,
    Trigger,
    TriggerKind,
)
from haven.core.consequence import ConsequenceClass
from haven.core.domain import Principal, RoleTier, RuleStatus


NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)


def _owner() -> Principal:
    return Principal(actor_id="owner-1", household_id="household-a", role_tier=RoleTier.OWNER)


def _event_spec() -> AutomationSpec:
    return AutomationSpec(
        spec_id="spec-1",
        household_id="household-a",
        trigger=Trigger(kind=TriggerKind.EVENT, parameters={"event_name": "task.status_changed", "to_state": "done"}),
        selector=Selector(parameters={"project_id": "project-1"}),
        action=ActionTarget(
            domain="computer",
            action="computer.noop",
            consequence_class=ConsequenceClass.REVERSIBLE_LOCAL,
            parameters={"resource_id": "file-1"},
        ),
        source_text="when the task is done, perform the local action",
        created_by="owner-1",
    )


def _feed() -> AutomationEventFeed:
    return AutomationEventFeed(source="tests", household_id="household-a", clock=lambda: NOW)


def _publish(feed: AutomationEventFeed):
    return feed.publish(
        event_id="task-event-1",
        event_name="task.status_changed",
        occurred_at=NOW,
        payload={"to_state": "done", "project_id": "project-1"},
    )


def test_resource_automation_lifecycle_and_restart_preserve_rules_and_audit(tmp_path):
    feed = _feed()
    service = ResourceAutomationService(
        path=tmp_path / "resource_automations.json",
        household_id="household-a",
        feed=feed,
        dispatch={"computer": lambda **_: {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    service.propose(_event_spec(), rule_id="rule-1")
    result = service.approve("rule-1", principal=_owner(), justification="approved for this test", now=NOW)
    assert result.rule.status is RuleStatus.APPROVED
    assert len(service.lifecycle_events()) == 1
    service.close()

    restored = ResourceAutomationService(
        path=tmp_path / "resource_automations.json",
        household_id="household-a",
        feed=_feed(),
        dispatch={"computer": lambda **_: {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    assert restored.rules()[0].status is RuleStatus.APPROVED
    assert restored.lifecycle_events()[0].transition == "approve"
    restored.close()


def test_event_delivery_and_dedup_survive_restart(tmp_path):
    calls: list[dict] = []
    path = tmp_path / "resource_automations.json"
    feed = _feed()
    service = ResourceAutomationService(
        path=path,
        household_id="household-a",
        feed=feed,
        dispatch={"computer": lambda **kwargs: calls.append(kwargs) or {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    service.propose(_event_spec(), rule_id="rule-1")
    service.approve("rule-1", principal=_owner(), justification="approved", now=NOW)
    event = _publish(feed)
    outcomes = service.process_pending()
    assert len(outcomes) == 1
    assert len(calls) == 1
    service.close()

    restored_feed = _feed()
    restored = ResourceAutomationService(
        path=path,
        household_id="household-a",
        feed=restored_feed,
        dispatch={"computer": lambda **kwargs: calls.append(kwargs) or {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    restored_feed.publish(
        event_id=event.event_id,
        event_name=event.event_name,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )
    assert restored.process_pending() == []
    assert len(calls) == 1
    restored.close()


def test_failed_dispatch_keeps_event_pending_for_recovery(tmp_path):
    path = tmp_path / "resource_automations.json"
    feed = _feed()
    failing = ResourceAutomationService(
        path=path,
        household_id="household-a",
        feed=feed,
        dispatch={"computer": lambda **_: (_ for _ in ()).throw(RuntimeError("provider stopped"))},
        clock=lambda: NOW,
    )
    failing.propose(_event_spec(), rule_id="rule-1")
    failing.approve("rule-1", principal=_owner(), justification="approved", now=NOW)
    _publish(feed)
    assert failing.process_pending() == []
    assert len(failing.pending_events()) == 1
    failing.close()

    recovered = ResourceAutomationService(
        path=path,
        household_id="household-a",
        feed=_feed(),
        dispatch={"computer": lambda **_: {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    assert len(recovered.process_pending()) == 1
    assert recovered.pending_events() == ()
    recovered.close()


def test_deadline_delivery_and_dedup_survive_restart(tmp_path):
    path = tmp_path / "resource_automations.json"
    item = AutomationDeadline(
        deadline_id="task:deadline-1",
        household_id="household-a",
        source_kind="task",
        source_id="task-1",
        due_at=NOW,
        payload={"task_id": "task-1", "priority": "high"},
    )
    spec = AutomationSpec(
        spec_id="deadline-spec",
        household_id="household-a",
        trigger=Trigger(kind=TriggerKind.DEADLINE, parameters={"source_kind": "task"}),
        selector=Selector(parameters={"priority": "high"}),
        action=ActionTarget(
            domain="computer",
            action="computer.noop",
            consequence_class=ConsequenceClass.REVERSIBLE_LOCAL,
            parameters={"resource_id": "file:deadline"},
        ),
        source_text="run at the task deadline",
        created_by="owner-1",
    )
    calls: list[dict] = []
    feed = _feed()
    service = ResourceAutomationService(
        path=path,
        household_id="household-a",
        feed=feed,
        deadline_provider=lambda: (item,),
        dispatch={"computer": lambda **kwargs: calls.append(kwargs) or {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    service.propose(spec, rule_id="deadline-rule")
    service.approve("deadline-rule", principal=_owner(), justification="approved", now=NOW)
    assert len(service.tick(now=NOW)) == 1
    service.close()

    restored = ResourceAutomationService(
        path=path,
        household_id="household-a",
        feed=_feed(),
        deadline_provider=lambda: (item,),
        dispatch={"computer": lambda **kwargs: calls.append(kwargs) or {"ok": True, "success": True}},
        clock=lambda: NOW,
    )
    assert restored.tick(now=NOW.replace(hour=22)) == []
    assert len(calls) == 1
    restored.close()
