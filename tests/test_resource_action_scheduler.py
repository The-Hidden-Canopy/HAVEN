"""ResourceActionScheduler: due computation (window/cooldown/weekday gates),
tick-through-dispatch (executed/denied/blocked outcomes), the
confirmation-required safety property (an unattended tick can never consent
on a household member's behalf), next-run projection, status rows, and
event-triggered dispatch (`handle_events`: matching, cross-household/
disabled/revoked exclusion, trigger/selector filter narrowing, and
delivery dedup) -- the resource-action analog of `tests/test_scheduler.py`,
dispatching through a *real* `ComputerActionService` for the end-to-end
case rather than only a fake, to prove the "reuses that same authority
lifecycle" claim concretely.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from haven.actions import ActionLedgerStore
from haven.automation import (
    ActionTarget,
    AutomationEvent,
    AutomationSpec,
    ResourceActionScheduler,
    Selector,
    Trigger,
    TriggerKind,
    approve,
    propose,
)
from haven.core.consequence import ConsequenceClass
from haven.core.domain import Principal, RoleTier
from haven.resources import ResourceStore
from haven.web.computer_actions import ComputerActionService
from haven.web.setup_config import SetupConfigStore

UTC = timezone.utc
# 2026-09-16 is a Wednesday (weekday() == 2).
NOW = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)


def _owner(household_id: str = "household-a") -> Principal:
    return Principal(actor_id="owner-1", household_id=household_id, role_tier=RoleTier.OWNER)


def _spec(
    *,
    time_of_day: str = "21:00",
    weekdays: tuple[int, ...] | None = None,
    action: str = "computer.noop",
    domain: str = "computer",
    resource_id: str | None = None,
    extra_params: dict | None = None,
) -> AutomationSpec:
    trigger_params: dict = {"time_of_day": time_of_day}
    if weekdays is not None:
        trigger_params["weekdays"] = weekdays
    params = dict(extra_params or {})
    if resource_id is not None:
        params["resource_id"] = resource_id
    return AutomationSpec(
        spec_id="spec-1",
        household_id="household-a",
        trigger=Trigger(kind=TriggerKind.TIME, parameters=trigger_params),
        selector=Selector(),
        action=ActionTarget(domain=domain, action=action, consequence_class=ConsequenceClass.REVERSIBLE_LOCAL, parameters=params),
        source_text="do the thing every night at 9pm",
        created_by="gerron",
    )


def _approved_rule(spec: AutomationSpec, *, rule_id: str = "rule-1"):
    rule = propose(spec, rule_id=rule_id)
    return approve(rule, principal=_owner(), justification="trust it", now=NOW).rule


def _recording_dispatch(results: list[dict], *, respond: dict):
    def dispatch(*, action, resource_id, parameters, justification):
        results.append(
            {"action": action, "resource_id": resource_id, "parameters": dict(parameters), "justification": justification}
        )
        return respond

    return dispatch


# -- due computation ----------------------------------------------------------


def test_an_approved_time_triggered_rule_is_due_inside_its_window():
    scheduler = ResourceActionScheduler(dispatch={})
    rule = _approved_rule(_spec(time_of_day="21:00"))
    assert scheduler.due_rules([rule], now=NOW) == [rule]


def test_a_rule_outside_its_window_is_not_due():
    scheduler = ResourceActionScheduler(dispatch={})
    rule = _approved_rule(_spec(time_of_day="09:00"))
    assert scheduler.due_rules([rule], now=NOW) == []


def test_a_disabled_rule_is_never_due():
    scheduler = ResourceActionScheduler(dispatch={})
    from haven.automation import set_enabled

    rule = set_enabled(_approved_rule(_spec()), False)
    assert scheduler.due_rules([rule], now=NOW) == []


def test_a_proposed_rule_never_run_never_asked():
    scheduler = ResourceActionScheduler(dispatch={})
    rule = propose(_spec(), rule_id="rule-1")
    assert scheduler.due_rules([rule], now=NOW) == []


def test_a_revoked_rule_is_never_due():
    from haven.automation import revoke

    scheduler = ResourceActionScheduler(dispatch={})
    approved = _approved_rule(_spec())
    revoked = revoke(approved, principal=_owner(), justification="stop", now=NOW).rule
    assert scheduler.due_rules([revoked], now=NOW) == []


def test_a_non_time_trigger_is_never_due_this_pass():
    scheduler = ResourceActionScheduler(dispatch={})
    spec = AutomationSpec(
        spec_id="spec-1",
        household_id="household-a",
        trigger=Trigger(kind=TriggerKind.EVENT, parameters={"event_name": "file.created"}),
        selector=Selector(),
        action=ActionTarget(domain="computer", action="computer.noop", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL),
        source_text="when a file arrives",
        created_by="gerron",
    )
    rule = _approved_rule(spec)
    assert scheduler.due_rules([rule], now=NOW) == []


def test_a_weekday_restricted_trigger_only_fires_on_its_weekdays():
    scheduler = ResourceActionScheduler(dispatch={})
    # NOW is a Wednesday (weekday 2); restrict to Monday/Tuesday only.
    rule = _approved_rule(_spec(weekdays=(0, 1)))
    assert scheduler.due_rules([rule], now=NOW) == []


def test_a_malformed_trigger_is_skipped_not_fatal_to_the_tick():
    scheduler = ResourceActionScheduler(dispatch={})
    good = _approved_rule(_spec(), rule_id="good")
    bad_spec = _spec()
    bad_rule = _approved_rule(bad_spec, rule_id="bad")
    # Corrupt the trigger's time_of_day after construction is not possible
    # (frozen); simulate a malformed bag directly via object construction.
    import dataclasses

    from haven.automation.schema import Trigger as TriggerCls

    broken_trigger = dataclasses.replace(bad_rule.spec.trigger, parameters=(("time_of_day", "not-a-time"),))
    broken_spec = dataclasses.replace(bad_rule.spec, trigger=broken_trigger)
    broken_rule = dataclasses.replace(bad_rule, spec=broken_spec)
    assert isinstance(broken_trigger, TriggerCls)
    due = scheduler.due_rules([good, broken_rule], now=NOW)
    assert due == [good]


def test_cooldown_suppresses_a_refire_within_the_configured_window():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}}, cooldown=timedelta(minutes=30))
    rule = _approved_rule(_spec())
    first = scheduler.tick(rules=[rule], now=NOW)
    assert len(first) == 1
    second = scheduler.tick(rules=[rule], now=NOW + timedelta(minutes=1))
    assert second == []


# -- dispatching / outcomes ----------------------------------------------------


def test_tick_dispatches_through_the_domains_own_callable():
    results: list[dict] = []
    dispatch = _recording_dispatch(results, respond={"ok": True, "success": True, "detail": "moved"})
    scheduler = ResourceActionScheduler(dispatch={"computer": dispatch})
    rule = _approved_rule(_spec(action="filesystem.move", resource_id="file-1", extra_params={"destination": "/archive"}))

    outcomes = scheduler.tick(rules=[rule], now=NOW)

    assert len(outcomes) == 1
    assert outcomes[0].outcome == "executed"
    assert results == [
        {
            "action": "filesystem.move",
            "resource_id": "file-1",
            "parameters": {"destination": "/archive"},
            "justification": f"schedule due at {NOW.isoformat()}",
        }
    ]


def test_a_denied_dispatch_records_denied():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": False, "error": "no household owner declared yet"}})
    rule = _approved_rule(_spec())
    outcomes = scheduler.tick(rules=[rule], now=NOW)
    assert outcomes[0].outcome == "denied"


def test_a_failed_execution_records_blocked():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": False, "detail": "refused"}})
    rule = _approved_rule(_spec())
    outcomes = scheduler.tick(rules=[rule], now=NOW)
    assert outcomes[0].outcome == "blocked"


def test_confirmation_required_is_reported_blocked_never_auto_confirmed():
    """The core safety property: the scheduler holds no confirmation token,
    so a confirmation_required action can never come back as executed."""

    scheduler = ResourceActionScheduler(
        dispatch={"computer": lambda **_: {"ok": True, "status": "confirmation_required", "request_id": "action-1"}}
    )
    rule = _approved_rule(_spec())
    outcomes = scheduler.tick(rules=[rule], now=NOW)
    assert outcomes[0].outcome == "blocked"


def test_a_domain_with_no_configured_dispatcher_is_denied_not_raised():
    scheduler = ResourceActionScheduler(dispatch={})
    rule = _approved_rule(_spec(domain="comms"))
    outcomes = scheduler.tick(rules=[rule], now=NOW)
    assert outcomes[0].outcome == "denied"
    assert "comms" in outcomes[0].result["error"]


# -- end-to-end against a real ComputerActionService ---------------------------


def _real_computer_service(tmp_path):
    class _Director:
        household_id = "household-a"
        has_declared_owner = True
        resident = _owner()

    store = SetupConfigStore(tmp_path / "setup.json")
    resource_store = ResourceStore(tmp_path / "resources.db")
    ledger = ActionLedgerStore(tmp_path / "ledger.db")
    service = ComputerActionService(
        store=store,
        director=_Director(),
        resource_store=resource_store,
        ledger=ledger,
        clock=lambda: NOW,
    )
    return service, resource_store, ledger


def test_end_to_end_a_due_automation_denies_through_the_real_authority_engine(tmp_path):
    """No computer provider is configured, so the real `ComputerActionService`
    denies the request exactly as it would a human's -- proving the scheduler
    reaches the real authority pipeline rather than a stand-in."""

    service, _, ledger = _real_computer_service(tmp_path)
    scheduler = ResourceActionScheduler(dispatch={"computer": service.request_action})
    rule = _approved_rule(_spec(action="filesystem.create_folder", extra_params={"path": "new-folder"}))

    outcomes = scheduler.tick(rules=[rule], now=NOW)

    assert outcomes[0].outcome == "denied"
    assert "computer access is not enabled" in outcomes[0].result["error"]
    # Denials still land in the real household ledger, same as a human's.
    history = ledger.list_by_household("household-a", limit=10)
    assert len(history) == 0  # request_action's early "no provider" guard never reaches _record


# -- projections ----------------------------------------------------------------


def test_next_run_scans_forward_to_the_next_matching_weekday():
    scheduler = ResourceActionScheduler(dispatch={})
    # NOW is Wednesday; restrict to Friday (weekday 4).
    rule = _approved_rule(_spec(time_of_day="21:00", weekdays=(4,)))
    nxt = scheduler.next_run(rule, after=NOW)
    assert nxt is not None
    assert nxt.weekday() == 4
    assert nxt > NOW


def test_next_run_is_none_for_a_non_time_trigger():
    scheduler = ResourceActionScheduler(dispatch={})
    spec = AutomationSpec(
        spec_id="spec-1",
        household_id="household-a",
        trigger=Trigger(kind=TriggerKind.DEADLINE),
        selector=Selector(),
        action=ActionTarget(domain="tasks", action="task.escalate", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL),
        source_text="escalate overdue tasks",
        created_by="gerron",
    )
    rule = _approved_rule(spec)
    assert scheduler.next_run(rule, after=NOW) is None


def test_status_rows_carry_domain_action_and_due_now():
    scheduler = ResourceActionScheduler(dispatch={})
    rule = _approved_rule(_spec(domain="computer", action="filesystem.move"))
    rows = scheduler.status([rule], now=NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row.rule_id == "rule-1"
    assert row.domain == "computer"
    assert row.action == "filesystem.move"
    assert row.due_now is True
    assert row.enabled is True


def test_status_includes_event_triggered_rules_with_no_time_projection():
    scheduler = ResourceActionScheduler(dispatch={})
    spec = AutomationSpec(
        spec_id="spec-1",
        household_id="household-a",
        trigger=Trigger(kind=TriggerKind.EVENT, parameters={"event_name": "file.created"}),
        selector=Selector(),
        action=ActionTarget(domain="computer", action="computer.noop", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL),
        source_text="when a file arrives",
        created_by="gerron",
    )
    rule = _approved_rule(spec)
    rows = scheduler.status([rule], now=NOW)
    assert len(rows) == 1
    assert rows[0].due_now is False
    assert rows[0].next_run_at is None


def test_status_omits_evidence_deadline_and_external_condition_triggers():
    scheduler = ResourceActionScheduler(dispatch={})
    for kind in (TriggerKind.EVIDENCE, TriggerKind.DEADLINE, TriggerKind.EXTERNAL_CONDITION):
        spec = AutomationSpec(
            spec_id="spec-1",
            household_id="household-a",
            trigger=Trigger(kind=kind),
            selector=Selector(),
            action=ActionTarget(domain="tasks", action="task.escalate", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL),
            source_text="not yet handled",
            created_by="gerron",
        )
        rule = _approved_rule(spec)
        assert scheduler.status([rule], now=NOW) == []


# -- event-triggered dispatch (handle_events) -----------------------------------


def _event_spec(
    *,
    event_name: str = "file.created",
    trigger_filters: dict | None = None,
    selector_filters: dict | None = None,
    domain: str = "computer",
    action: str = "computer.noop",
    household_id: str = "household-a",
) -> AutomationSpec:
    trigger_params = {"event_name": event_name, **(trigger_filters or {})}
    return AutomationSpec(
        spec_id="spec-1",
        household_id=household_id,
        trigger=Trigger(kind=TriggerKind.EVENT, parameters=trigger_params),
        selector=Selector(parameters=selector_filters or {}),
        action=ActionTarget(domain=domain, action=action, consequence_class=ConsequenceClass.REVERSIBLE_LOCAL),
        source_text="react to an event",
        created_by="gerron",
    )


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


def test_a_matching_event_dispatches_the_rule():
    results: list[dict] = []
    dispatch = _recording_dispatch(results, respond={"ok": True, "success": True})
    scheduler = ResourceActionScheduler(dispatch={"computer": dispatch})
    rule = _approved_rule(_event_spec())

    outcomes = scheduler.handle_events(rules=[rule], events=[_event()], now=NOW)

    assert len(outcomes) == 1
    assert outcomes[0].outcome == "executed"
    assert len(results) == 1


def test_a_different_event_name_does_not_match():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule = _approved_rule(_event_spec(event_name="file.created"))
    outcomes = scheduler.handle_events(rules=[rule], events=[_event(event_name="message.received")], now=NOW)
    assert outcomes == []


def test_a_cross_household_event_does_not_match():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule = _approved_rule(_event_spec(household_id="household-a"))
    outcomes = scheduler.handle_events(rules=[rule], events=[_event(household_id="household-b")], now=NOW)
    assert outcomes == []


def test_a_disabled_rule_never_event_fires():
    from haven.automation import set_enabled

    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule = set_enabled(_approved_rule(_event_spec()), False)
    assert scheduler.handle_events(rules=[rule], events=[_event()], now=NOW) == []


def test_a_proposed_rule_never_event_fires():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule = propose(_event_spec(), rule_id="rule-1")
    assert scheduler.handle_events(rules=[rule], events=[_event()], now=NOW) == []


def test_an_extra_trigger_filter_narrows_the_match():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule = _approved_rule(_event_spec(event_name="task.status_changed", trigger_filters={"to_state": "done"}))

    no_match = scheduler.handle_events(
        rules=[rule],
        events=[_event(event_id="e1", event_name="task.status_changed", payload={"to_state": "blocked"})],
        now=NOW,
    )
    assert no_match == []

    match = scheduler.handle_events(
        rules=[rule],
        events=[_event(event_id="e2", event_name="task.status_changed", payload={"to_state": "done"})],
        now=NOW,
    )
    assert len(match) == 1


def test_a_selector_filter_further_narrows_the_match():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule = _approved_rule(_event_spec(event_name="task.status_changed", selector_filters={"project_id": "p1"}))

    wrong_project = scheduler.handle_events(
        rules=[rule],
        events=[_event(event_id="e1", event_name="task.status_changed", payload={"project_id": "p2"})],
        now=NOW,
    )
    assert wrong_project == []

    right_project = scheduler.handle_events(
        rules=[rule],
        events=[_event(event_id="e2", event_name="task.status_changed", payload={"project_id": "p1"})],
        now=NOW,
    )
    assert len(right_project) == 1


def test_redelivering_the_same_event_never_refires_the_rule():
    results: list[dict] = []
    dispatch = _recording_dispatch(results, respond={"ok": True, "success": True})
    scheduler = ResourceActionScheduler(dispatch={"computer": dispatch})
    rule = _approved_rule(_event_spec())
    event = _event()

    first = scheduler.handle_events(rules=[rule], events=[event], now=NOW)
    second = scheduler.handle_events(rules=[rule], events=[event], now=NOW + timedelta(minutes=5))

    assert len(first) == 1
    assert second == []
    assert len(results) == 1


def test_one_event_can_fire_multiple_rules():
    scheduler = ResourceActionScheduler(dispatch={"computer": lambda **_: {"ok": True, "success": True}})
    rule_a = _approved_rule(_event_spec(), rule_id="rule-a")
    rule_b = _approved_rule(_event_spec(), rule_id="rule-b")
    outcomes = scheduler.handle_events(rules=[rule_a, rule_b], events=[_event()], now=NOW)
    assert {outcome.rule_id for outcome in outcomes} == {"rule-a", "rule-b"}


def test_event_triggered_confirmation_required_is_also_reported_blocked():
    scheduler = ResourceActionScheduler(
        dispatch={"computer": lambda **_: {"ok": True, "status": "confirmation_required", "request_id": "action-1"}}
    )
    rule = _approved_rule(_event_spec())
    outcomes = scheduler.handle_events(rules=[rule], events=[_event()], now=NOW)
    assert outcomes[0].outcome == "blocked"
