"""Domain-independent AutomationSpec (native product-consolidation plan,
P1 "Cross-Domain Automation" §6.2): Trigger/Selector/ActionTarget, tested
against every trigger kind the plan's own table names, and proving the
schema grants no authority on its own (it is data, no execution method)."""

from __future__ import annotations

import pytest

from haven.automation import ActionTarget, AutomationSpec, Selector, Trigger, TriggerKind
from haven.core.consequence import ConsequenceClass


def test_every_planned_trigger_kind_constructs():
    for kind in TriggerKind:
        trigger = Trigger(kind=kind, parameters={"example": "value"})
        assert trigger.kind is kind
        assert trigger.as_dict() == {"example": "value"}


def test_trigger_rejects_a_non_trigger_kind():
    with pytest.raises(ValueError):
        Trigger(kind="time")  # a raw string, not the enum


def test_trigger_accepts_a_tuple_or_mapping_parameter_bag_identically():
    from_dict = Trigger(kind=TriggerKind.TIME, parameters={"weekdays": ("mon", "wed"), "time": "21:00"})
    from_tuple = Trigger(kind=TriggerKind.TIME, parameters=(("weekdays", ("mon", "wed")), ("time", "21:00")))
    assert from_dict.parameters == from_tuple.parameters


def test_selector_scopes_the_trigger_independently():
    selector = Selector(parameters={"room": "office", "weekdays": ("mon", "tue")})
    assert selector.as_dict()["room"] == "office"


def test_action_target_requires_domain_action_and_consequence_class():
    action = ActionTarget(
        domain="computer",
        action="filesystem.move",
        consequence_class=ConsequenceClass.REVERSIBLE_LOCAL,
        parameters={"destination": "/archive"},
    )
    assert action.domain == "computer"
    assert action.action == "filesystem.move"
    assert action.consequence_class is ConsequenceClass.REVERSIBLE_LOCAL


def test_action_target_rejects_blank_domain_or_action():
    with pytest.raises(ValueError):
        ActionTarget(domain="", action="filesystem.move", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL)
    with pytest.raises(ValueError):
        ActionTarget(domain="computer", action="", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL)


def test_action_target_rejects_a_non_consequence_class():
    with pytest.raises(ValueError):
        ActionTarget(domain="computer", action="filesystem.move", consequence_class="high_impact")


def test_automation_spec_composes_trigger_selector_and_action():
    spec = AutomationSpec(
        spec_id="spec-1", household_id="hh-1",
        trigger=Trigger(kind=TriggerKind.TIME, parameters={"time": "21:00"}),
        selector=Selector(parameters={"room": "office"}),
        action=ActionTarget(
            domain="home", action="device.turn_off", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL
        ),
        source_text="turn off the office lamp every night at 9pm",
        created_by="gerron",
    )
    assert spec.trigger.kind is TriggerKind.TIME
    assert spec.action.domain == "home"
    assert spec.created_by == "gerron"


def test_automation_spec_rejects_wrong_component_types():
    valid_trigger = Trigger(kind=TriggerKind.TIME)
    valid_selector = Selector()
    valid_action = ActionTarget(domain="home", action="a", consequence_class=ConsequenceClass.READ_ONLY)

    with pytest.raises(ValueError):
        AutomationSpec(
            spec_id="s", household_id="hh-1", trigger="not a trigger", selector=valid_selector, action=valid_action,
            source_text="t", created_by="u",
        )
    with pytest.raises(ValueError):
        AutomationSpec(
            spec_id="s", household_id="hh-1", trigger=valid_trigger, selector="not a selector", action=valid_action,
            source_text="t", created_by="u",
        )
    with pytest.raises(ValueError):
        AutomationSpec(
            spec_id="s", household_id="hh-1", trigger=valid_trigger, selector=valid_selector, action="not an action target",
            source_text="t", created_by="u",
        )


def test_automation_spec_requires_non_blank_identity_fields():
    valid_trigger = Trigger(kind=TriggerKind.EVENT)
    valid_selector = Selector()
    valid_action = ActionTarget(domain="tasks", action="task.create", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL)

    for field_name in ("spec_id", "household_id", "source_text", "created_by"):
        kwargs = dict(
            spec_id="s", household_id="hh-1", trigger=valid_trigger, selector=valid_selector, action=valid_action,
            source_text="t", created_by="u",
        )
        kwargs[field_name] = ""
        with pytest.raises(ValueError):
            AutomationSpec(**kwargs)


def test_the_schema_grants_no_execution_capability_of_its_own():
    """A contract-only assertion: AutomationSpec has no run()/execute()
    method anywhere on it -- constructing one does nothing by itself,
    matching this module's own "contract only, this pass" scope."""

    spec = AutomationSpec(
        spec_id="spec-1", household_id="hh-1",
        trigger=Trigger(kind=TriggerKind.DEADLINE),
        selector=Selector(),
        action=ActionTarget(domain="tasks", action="task.escalate", consequence_class=ConsequenceClass.REVERSIBLE_LOCAL),
        source_text="escalate overdue tasks",
        created_by="gerron",
    )
    assert not hasattr(spec, "run")
    assert not hasattr(spec, "execute")
