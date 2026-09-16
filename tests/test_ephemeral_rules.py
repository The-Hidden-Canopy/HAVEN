"""Ephemeral rules: "turn this fan off in 40 minutes" needs no new engine.

A `RuleDraft.expires_at` reuses the exact same propose -> approve -> run_rule
path as a permanent rule. The only difference is that `AuthorityEngine.decide()`
denies with RULE_EXPIRED once `now` passes it -- there is no separate
"temporary rule" type or expiry-checking loop.
"""

from dataclasses import replace
from datetime import timedelta

from haven.core.domain import ContextState, DeviceState, PresenceState, WorldSnapshot
from test_vertical_slice import BASE_TIME, _explicit_draft, _runtime


def _long_lived_world(principal) -> WorldSnapshot:
    """A world snapshot valid for hours, so only rule expiry -- not evidence
    staleness -- is under test here. `test_vertical_slice._world()` is fixed
    to a 10-minute validity window, too short for the minute-40+ scenarios
    this file needs.
    """

    return WorldSnapshot(
        snapshot_id="ephemeral-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(hours=2),
        presence=(
            PresenceState(
                person_id=principal.actor_id,
                room_id="bedroom",
                present=True,
                observed_at=BASE_TIME,
                source="fixture.presence",
            ),
        ),
        contexts=(
            ContextState(
                context_id="working_late",
                active=True,
                observed_at=BASE_TIME,
                source="fixture.context",
            ),
        ),
        devices=(
            DeviceState(
                device_id="bedroom_lights",
                kind="light",
                room_id="bedroom",
                is_on=True,
                brightness_pct=80,
                observed_at=BASE_TIME,
                source="fixture.home_assistant_state",
            ),
        ),
    )


def _approve(runtime, draft, *, resident, owner):
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id,
        principal=owner,
        justification="Owner approval for the ephemeral rule.",
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rule


def test_rule_with_no_expiry_is_unaffected():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _explicit_draft(resident), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_long_lived_world(resident),
        justification="Permanent rule, no expiry.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"


def test_rule_still_runs_before_its_expiry():
    runtime, store, adapter, resident, owner = _runtime()
    draft = replace(_explicit_draft(resident), expires_at=BASE_TIME + timedelta(minutes=40))
    rule = _approve(runtime, draft, resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_long_lived_world(resident),
        justification="Run the fan-off rule while it is still valid.",
        now=BASE_TIME + timedelta(minutes=20),
    )

    assert receipt.outcome == "executed"


def test_rule_is_denied_once_its_expiry_has_passed():
    runtime, store, adapter, resident, owner = _runtime()
    draft = replace(_explicit_draft(resident), expires_at=BASE_TIME + timedelta(minutes=40))
    rule = _approve(runtime, draft, resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id,
        principal=resident,
        world=_long_lived_world(resident),
        justification="Attempt to run the fan-off rule after it expired.",
        now=BASE_TIME + timedelta(minutes=45),
    )

    assert receipt.decision.code.value == "rule_expired"
    assert adapter.commands == ()
