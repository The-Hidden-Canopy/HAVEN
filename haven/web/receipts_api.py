"""Trust-chain drill-down payloads for one governed action.

``action_chain()`` assembles the six-section review surface (request,
interpretation, evidence, authority, execution, consequence) plus the
correlated timeline from what the store actually holds: the ``ActionRecord``
is authoritative state, an ``ActionReceipt`` (matched by request id) enriches
it with interpretation and evidence, and the execution service/provider come
from the recorded device command or the device manifest. Anything the store
does not carry is reported as unavailable rather than invented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from haven.authority.policy import risk_for_request
from haven.core.domain import ActionOrigin, ActionStatus, EventType
from haven.core.store import HavenStore

TIMELINE_LIMIT = 20
# Evidence observed within this window of ``now`` reads as fresh; it matches
# the demo snapshot validity window instead of inventing a new threshold.
FRESH_WINDOW_SECONDS = 30.0

_ACTION_EVENT_TYPES = (EventType.ACTION_AUTHORIZED, EventType.ACTION_EXECUTED, EventType.ACTION_BLOCKED)


def _seconds_between(later: datetime, earlier: datetime) -> float | None:
    try:
        return max(0.0, (later - earlier).total_seconds())
    except TypeError:
        return None


def _with_relative(at: datetime | None, now: datetime | None) -> dict[str, Any]:
    """ISO stamp plus a ``seconds_ago`` when both sides can be compared."""

    if at is None:
        return {"at": None, "seconds_ago": None}
    entry: dict[str, Any] = {"at": at.isoformat(), "seconds_ago": None}
    if now is not None:
        entry["seconds_ago"] = _seconds_between(now, at)
    return entry


def _find_receipt(action, receipts: Iterable | None):
    if not receipts:
        return None
    return next(
        (
            receipt
            for receipt in receipts
            if receipt.requested_action.request_id == action.request.request_id
        ),
        None,
    )


def _rule_source(store: HavenStore, rule_id: str) -> str | None:
    try:
        rule = store.get_rule(rule_id)
    except KeyError:
        return None
    return " ".join(rule.draft.source_text.split())


def _interpretation_section(action, receipt, store: HavenStore) -> dict[str, Any]:
    request = action.request
    if receipt is not None and receipt.interpretation:
        # For a rule action the receipt carries the draft interpretation; for
        # a direct action it carries the human's stated justification -- the
        # runtime puts whichever exists there, and origin says which.
        basis = "rule_interpretation" if request.origin is ActionOrigin.RULE else "direct_justification"
        return {
            "text": receipt.interpretation,
            "basis": basis,
            "source_text": _rule_source(store, request.rule_id) if request.origin is ActionOrigin.RULE else None,
        }
    source = _rule_source(store, request.rule_id) if request.origin is ActionOrigin.RULE else None
    if source:
        return {"text": None, "basis": "rule_source", "source_text": source}
    return {"text": None, "basis": "unavailable", "source_text": None}


def _evidence_section(receipt, now: datetime | None) -> list[dict[str, Any]]:
    if receipt is None:
        return []
    rows = []
    for ref in receipt.evidence:
        observed = _with_relative(ref.observed_at, now)
        age = observed["seconds_ago"]
        rows.append(
            {
                "kind": ref.kind,
                "subject": ref.subject_id,
                "status": ref.status.value,
                "observed_at": observed["at"],
                "observed_seconds_ago": age,
                "fresh": age is not None and age <= FRESH_WINDOW_SECONDS,
                # EvidenceRef carries no confidence; report the gap, not a guess.
                "confidence": None,
                "note": f"source {ref.source} · confidence is not recorded on evidence refs",
            }
        )
    return rows


def _authority_section(action, device_registry) -> dict[str, Any]:
    decision = action.decision
    risk_tier = None
    risk_note = None
    if device_registry is not None:
        # The receipt does not record risk; reclassifying the request through
        # the same risk_for_request path the engine used is the honest view
        # of the decision code path, labeled as such.
        risk, _ = risk_for_request(action.request, device_registry=device_registry)
        risk_tier = risk.value
        risk_note = "classified from the device's declared capability at view time; the receipt does not record risk"
    else:
        risk_note = "risk tier is not recorded on the receipt and no device registry was provided"
    return {
        "status": decision.status.value,
        "code": decision.code.value,
        "explanation": decision.explanation,
        "required_role": decision.required_role.name if decision.required_role is not None else None,
        "risk_tier": risk_tier,
        "risk_note": risk_note,
    }


def _service_for(action, device_registry, executed_commands, service_for_kind=None) -> tuple[str | None, str, str | None]:
    """Resolve (service, basis, provider_id) from recorded execution data.

    Order of honesty: the device command the adapter actually executed, then
    the device manifest's declared service for the named capability, then the
    deployment's ActionKind-to-service routing (live adapters record no local
    command log, so a successfully executed action would otherwise report
    "unavailable" for a routing decision the runtime already made).
    """

    request = action.request
    for command in executed_commands or ():
        if command.request_id == request.request_id:
            provider_id = None
            if device_registry is not None and device_registry.is_registered(command.target_device_id):
                provider_id = device_registry.get(command.target_device_id).provider_id
            return command.service, "executed_command", provider_id
    provider_id = None
    manifest = None
    if device_registry is not None:
        try:
            manifest = device_registry.get(request.target_device_id)
        except Exception:
            manifest = None
        if manifest is not None:
            provider_id = manifest.provider_id
    if manifest is not None and request.capability is not None:
        try:
            return manifest.capability(request.capability).service, "device_capability", provider_id
        except Exception:
            pass
    if service_for_kind is not None:
        service = service_for_kind(request.action_kind)
        if service:
            return service, "action_kind", provider_id
    return None, "unavailable", provider_id


def _execution_section(action, device_registry, executed_commands, service_for_kind, now: datetime | None) -> dict[str, Any]:
    result = action.result
    service, service_basis, provider_id = _service_for(action, device_registry, executed_commands, service_for_kind)
    executed = _with_relative(action.executed_at, now)
    observed = _with_relative(result.observed_at, now) if result is not None else {"at": None, "seconds_ago": None}
    return {
        "attempted": result is not None or action.status is ActionStatus.EXECUTED,
        "service": service,
        "service_basis": service_basis,
        "provider_id": provider_id,
        "success": result.success if result is not None else None,
        "detail": result.detail if result is not None else None,
        "executed_at": executed["at"],
        "executed_seconds_ago": executed["seconds_ago"],
        "observed_at": observed["at"],
        "source": result.source if result is not None else None,
    }


def _consequence_section(action, store: HavenStore, now: datetime | None) -> dict[str, Any]:
    executed_event = next(
        (
            event
            for event in store.events
            if event.event_type is EventType.ACTION_EXECUTED and dict(event.payload).get("action_id") == action.action_id
        ),
        None,
    )
    if executed_event is None:
        return {
            "observed": False,
            "summary": None,
            "observed_at": None,
            "observed_seconds_ago": None,
            "delay_ms": None,
            "note": "not yet observed",
        }
    result = action.result
    detail = result.detail if result is not None else f"success={dict(executed_event.payload).get('success')}"
    delay_ms = None
    if action.executed_at is not None:
        seconds = _seconds_between(executed_event.occurred_at, action.executed_at)
        delay_ms = None if seconds is None else round(seconds * 1000.0, 3)
    observed = _with_relative(executed_event.occurred_at, now)
    return {
        "observed": True,
        "summary": f"{action.request.target_device_id} reported: {detail}",
        "observed_at": observed["at"],
        "observed_seconds_ago": observed["seconds_ago"],
        "delay_ms": delay_ms,
        "note": None,
    }


def _event_summary(event) -> str:
    payload = dict(event.payload)
    event_type = event.event_type
    device = payload.get("target_device_id")
    if event_type == EventType.ACTION_AUTHORIZED:
        return f"authorized on {device}"
    if event_type == EventType.ACTION_EXECUTED:
        return f"executed on {device} (success={payload.get('success')})"
    if event_type == EventType.ACTION_BLOCKED:
        return f"blocked on {device} ({payload.get('code')})"
    if event_type == EventType.RULE_PROPOSED:
        return f"rule proposed: {payload.get('rule_id')}"
    if event_type == EventType.RULE_APPROVED:
        return f"rule approved: {payload.get('rule_id')}"
    if event_type == EventType.RULE_CLARIFIED:
        return f"rule clarified: {payload.get('rule_id')}"
    if event_type in (EventType.RULE_APPROVAL_BLOCKED, EventType.RULE_CLARIFICATION_BLOCKED):
        return f"rule blocked: {payload.get('rule_id')} ({payload.get('code')})"
    return event_type.value


def _timeline_section(action, store: HavenStore, now: datetime | None) -> list[dict[str, Any]]:
    # Rule actions correlate by rule id (the runtime's declared correlation
    # key), so correlated events can include the rule lifecycle and sibling
    # firings; this action's own events lead, the rest follow, bounded.
    correlated = [event for event in store.events if event.correlation_id == action.request.rule_id]
    own = [event for event in correlated if dict(event.payload).get("action_id") == action.action_id]
    own_ids = {event.event_id for event in own}
    ordered = own + [event for event in correlated if event.event_id not in own_ids]
    rows = []
    for event in ordered[:TIMELINE_LIMIT]:
        occurred = _with_relative(event.occurred_at, now)
        rows.append(
            {
                "at": occurred["at"],
                "seconds_ago": occurred["seconds_ago"],
                "event_type": event.event_type.value,
                "summary": _event_summary(event),
            }
        )
    return rows


def action_chain(
    action_id: str,
    *,
    store: HavenStore,
    now: datetime | None = None,
    receipts: Iterable | None = None,
    device_registry=None,
    executed_commands: Iterable | None = None,
    service_for_kind=None,
) -> dict[str, Any] | None:
    """Build the six-section trust chain for one action, or None if unknown.

    ``store`` is required and sufficient; ``receipts`` (the runtime's
    receipt ledger), ``device_registry``, and ``executed_commands`` (device
    commands recorded by the execution adapter) enrich sections the bare
    ActionRecord cannot cover. ``service_for_kind`` (ActionKind -> service
    str) is the fallback routing map for live adapters that record no local
    command log. ``now`` enables relative timestamps; without it only ISO
    stamps are emitted.
    """

    try:
        action = store.get_action(action_id)
    except KeyError:
        return None
    receipt = _find_receipt(action, receipts)
    request = action.request
    requested = _with_relative(request.requested_at, now)
    parameters = {key: value for key, value in request.parameters}
    return {
        "action_id": action.action_id,
        "status": action.status.value,
        "outcome": receipt.outcome if receipt is not None else action.status.value,
        "request": {
            "action_kind": request.action_kind.value,
            "capability": request.capability,
            "target_device_id": request.target_device_id,
            "parameters": parameters,
            "origin": request.origin.value,
            "rule_id": request.rule_id if request.origin is ActionOrigin.RULE else None,
            "actor": request.requested_by,
            "justification": request.justification,
            "evidence_snapshot_id": request.evidence_snapshot_id,
            "confirmation_present": request.confirmation_token is not None,
            "requested_at": requested["at"],
            "requested_seconds_ago": requested["seconds_ago"],
        },
        "interpretation": _interpretation_section(action, receipt, store),
        "evidence": _evidence_section(receipt, now),
        "authority": _authority_section(action, device_registry),
        "execution": _execution_section(action, device_registry, executed_commands, service_for_kind, now),
        "consequence": _consequence_section(action, store, now),
        "timeline": _timeline_section(action, store, now),
    }


def event_action_id(store: HavenStore, event_id: str) -> str | None:
    """Resolve an activity row's event to its action id, or None.

    Only action-correlated events (authorized/executed/blocked) carry an
    ``action_id`` payload key; every other event type returns None and the
    row gets no drill-down affordance.
    """

    for event in store.events:
        if event.event_id != event_id:
            continue
        if event.event_type not in _ACTION_EVENT_TYPES:
            return None
        action_id = dict(event.payload).get("action_id")
        return str(action_id) if action_id else None
    return None


__all__ = ["action_chain", "event_action_id", "TIMELINE_LIMIT", "FRESH_WINDOW_SECONDS"]
