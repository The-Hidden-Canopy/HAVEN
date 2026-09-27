"""HavenApplication.request_action_as/confirm_pending_as/deny_pending_as.

The External Agent Gateway's action-request path: mirrors `device_command`'s
validation and reuses the same `runtime.run_action` authority call, but
takes an explicit principal instead of assuming `self.resident`, and never
triggers `_say` (a physical local voice announcement -- wrong for a request
that arrived through a different conversation entirely, per ADR-002).
"""

from datetime import datetime, timezone

from haven.core.domain import DecisionStatus, Principal, RoleTier
from haven.web.demo import HOUSEHOLD_ID, DemoDirector

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)

BOUND_PERSON = Principal(actor_id="ada", household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER)
ANOTHER_PERSON = Principal(actor_id="babbage", household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER)

EXTERNAL_SOURCE = (("provider", "alexa_plus"), ("connection_id", "extconn-1"))


def _director() -> DemoDirector:
    return DemoDirector(clock=lambda: NOW)


def test_allowed_action_executes_with_the_given_principal_as_actor():
    director = _director()

    result = director.request_action_as(BOUND_PERSON, "office_light", "light.turn_off")

    assert result["ok"] is True
    assert result["status"] == "executed"
    action = next(a for a in director.store.state.actions if a.status.value == "executed")
    assert action.request.requested_by == "ada"


def test_allowed_action_attaches_external_source_to_the_receipt():
    director = _director()

    director.request_action_as(BOUND_PERSON, "office_light", "light.turn_off", external_source=EXTERNAL_SOURCE)

    receipt = director.receipts[-1]
    assert receipt.external_source == EXTERNAL_SOURCE


def test_allowed_action_never_speaks_or_glows_for_a_resident():
    director = _director()
    before = len(director._conversation)  # noqa: SLF001 -- verifying no physical announcement fired

    director.request_action_as(BOUND_PERSON, "office_light", "light.turn_off")

    assert len(director._conversation) == before  # noqa: SLF001


def test_confirmation_required_creates_a_pending_request_tagged_with_the_principal():
    director = _director()

    result = director.request_action_as(
        BOUND_PERSON, "garage_door", "cover.close", external_connection_id="extconn-1"
    )

    assert result["ok"] is True
    assert result["status"] == "confirmation_required"
    request_id = result["request_id"]
    pending = director._pending[request_id]  # noqa: SLF001
    assert pending.requested_by == "ada"
    assert pending.external_connection_id == "extconn-1"
    # Not executed yet.
    assert all(a.status.value != "executed" for a in director.store.state.actions)


def test_unknown_device_is_a_business_failure():
    director = _director()

    result = director.request_action_as(BOUND_PERSON, "nonexistent-device", "light.turn_off")

    assert result == {"ok": False, "error": "unknown device"}


def test_unsupported_service_is_a_business_failure():
    director = _director()

    result = director.request_action_as(BOUND_PERSON, "office_light", "lock.unlock")

    assert result == {"ok": False, "error": "unsupported service"}


def test_confirm_pending_as_the_same_principal_executes_once():
    director = _director()
    created = director.request_action_as(BOUND_PERSON, "garage_door", "cover.close")
    request_id = created["request_id"]

    result = director.confirm_pending_as(BOUND_PERSON, request_id, external_source=EXTERNAL_SOURCE)

    assert result["ok"] is True
    assert result["status"] == "confirmed"
    action = next(a for a in director.store.state.actions if a.status.value == "executed")
    assert action.request.requested_by == "ada"
    receipt = director.receipts[-1]
    assert receipt.external_source == EXTERNAL_SOURCE


def test_confirm_pending_as_a_different_principal_is_refused():
    director = _director()
    created = director.request_action_as(BOUND_PERSON, "garage_door", "cover.close")
    request_id = created["request_id"]

    result = director.confirm_pending_as(ANOTHER_PERSON, request_id)

    assert result == {"ok": False, "error": "principal mismatch"}
    assert request_id in director._pending  # noqa: SLF001 -- still pending, not consumed


def test_confirm_pending_as_an_unknown_request_id_is_refused():
    director = _director()

    result = director.confirm_pending_as(BOUND_PERSON, "not-a-real-request")

    assert result == {"ok": False, "error": "unknown request"}


def test_confirm_pending_as_cannot_reuse_an_already_confirmed_handle():
    director = _director()
    created = director.request_action_as(BOUND_PERSON, "garage_door", "cover.close")
    request_id = created["request_id"]
    first = director.confirm_pending_as(BOUND_PERSON, request_id)
    assert first["ok"] is True

    second = director.confirm_pending_as(BOUND_PERSON, request_id)

    assert second == {"ok": False, "error": "unknown request"}


def test_deny_pending_as_the_same_principal_clears_it_without_speaking():
    director = _director()
    created = director.request_action_as(BOUND_PERSON, "garage_door", "cover.close")
    request_id = created["request_id"]
    before = len(director._conversation)  # noqa: SLF001

    result = director.deny_pending_as(BOUND_PERSON, request_id)

    assert result == {"ok": True, "status": "denied"}
    assert request_id not in director._pending  # noqa: SLF001
    assert len(director._conversation) == before  # noqa: SLF001 -- no "No, leave it." announcement


def test_deny_pending_as_a_different_principal_is_refused():
    director = _director()
    created = director.request_action_as(BOUND_PERSON, "garage_door", "cover.close")
    request_id = created["request_id"]

    result = director.deny_pending_as(ANOTHER_PERSON, request_id)

    assert result == {"ok": False, "error": "principal mismatch"}
    assert request_id in director._pending  # noqa: SLF001
