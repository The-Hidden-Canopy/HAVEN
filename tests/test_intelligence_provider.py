"""Contract and boundary tests for the proposal-only intelligence seam.

The seam is agent-agnostic: HAVEN selects an intelligence provider through
the capability registry by kind="intelligence" and required capabilities,
never by vendor. Every method proposes; none can touch the store, execution
adapters, the device registry, or permissions.
"""

import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from haven.core.domain import AuthorityDecision, DecisionCode, DecisionStatus, Principal, RoleTier, RuleDraft
from haven.core.store import HavenStore
from haven.execution import ExecutionProviderRegistry
from haven.intelligence.gateway import (
    AgentContext,
    AgentResponse,
    IntelligenceProvider,
    ScriptedIntelligenceProvider,
    UnsupportedIntent,
)
from haven.intelligence.intents import ActionProposal, ClarificationRequest, QueryRequest
from haven.runtime import HavenRuntime

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
EXAMPLE = "When I'm working late, don't blast the bedroom lights when I walk in."

RESIDENT = Principal(actor_id="resident-1", household_id="household-1", role_tier=RoleTier.MEMBER)


def _context() -> AgentContext:
    return AgentContext(
        household_id=RESIDENT.household_id,
        actor_id=RESIDENT.actor_id,
        actor_role=RESIDENT.role_tier.name,
        room_focus="bedroom",
        recent_lines=("hello", "working late again?"),
    )


def test_interpret_proposes_the_example_and_nothing_else() -> None:
    provider = ScriptedIntelligenceProvider()

    draft = provider.interpret(EXAMPLE, principal=RESIDENT, now=NOW)

    assert isinstance(draft, RuleDraft)
    assert draft.source_text == EXAMPLE
    assert draft.household_id == RESIDENT.household_id
    assert draft.proposed_by == RESIDENT.actor_id
    assert draft.unresolved
    with pytest.raises(UnsupportedIntent):
        provider.interpret("dim the kitchen lights", principal=RESIDENT, now=NOW)


def test_chat_returns_a_canned_honest_reply_or_raises() -> None:
    provider = ScriptedIntelligenceProvider()

    reply = provider.chat(_context(), EXAMPLE)

    assert isinstance(reply, AgentResponse)
    assert reply.text.strip()
    with pytest.raises(UnsupportedIntent):
        provider.chat(_context(), "play some jazz")


def test_propose_rule_uses_the_bounded_context_identity() -> None:
    provider = ScriptedIntelligenceProvider()

    draft = provider.propose_rule(_context(), EXAMPLE)

    assert draft.household_id == RESIDENT.household_id
    assert draft.proposed_by == RESIDENT.actor_id
    with pytest.raises(UnsupportedIntent):
        provider.propose_rule(_context(), "dim the kitchen lights")


def test_explain_rewords_the_decision_for_a_human() -> None:
    provider = ScriptedIntelligenceProvider()
    decision = AuthorityDecision(
        status=DecisionStatus.DENY,
        code=DecisionCode.WRONG_ROLE_TIER,
        explanation="only a household owner can approve an autonomous rule",
        required_role=RoleTier.OWNER,
    )

    reply = provider.explain(_context(), decision)

    assert isinstance(reply, AgentResponse)
    assert "only a household owner can approve an autonomous rule" in reply.text
    assert decision.explanation in reply.text


def test_agent_response_and_context_are_frozen_plain_data() -> None:
    response = AgentResponse(text="hello")
    context = _context()
    with pytest.raises(FrozenInstanceError):
        response.text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.actor_id = "someone-else"  # type: ignore[misc]
    with pytest.raises(ValueError):
        AgentResponse(text="  ")
    with pytest.raises(ValueError):
        AgentContext(household_id="h", actor_id="", actor_role="member")


def test_provider_protocol_has_no_mutation_surface() -> None:
    """The seam's method signatures must never reference stateful internals."""

    forbidden = ("HavenStore", "ExecutionAdapter", "ExecutionProviderRegistry", "DeviceRegistry", "AuthorityEngine")
    for name in ("interpret", "chat", "propose_rule", "explain", "interpret_intent"):
        method = getattr(IntelligenceProvider, name)
        signature = inspect.signature(method)
        annotations = [str(value) for value in signature.parameters.values()]
        annotations.append(str(signature.return_annotation))
        joined = " ".join(annotations)
        for token in forbidden:
            assert token not in joined, f"{name} leaks {token} into the seam"


def test_fixture_provider_carries_no_state() -> None:
    """The zero-config provider has no attribute path to a store or adapter."""

    provider = ScriptedIntelligenceProvider()
    assert vars(provider) == {}


def test_runtime_only_passes_plain_arguments_to_the_provider() -> None:
    """propose_from_text hands the provider text, principal, and time -- nothing else."""

    seen: list[tuple[str, object]] = []

    class _RecordingProvider:
        def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft:
            seen.append(("text", text))
            seen.append(("principal", principal))
            seen.append(("now", now))
            raise UnsupportedIntent

    store = HavenStore(household_id=RESIDENT.household_id)
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=_RecordingProvider(),
        execution_providers=ExecutionProviderRegistry(),
    )

    with pytest.raises(UnsupportedIntent):
        runtime.propose_from_text(EXAMPLE, principal=RESIDENT, now=NOW)

    assert [name for name, _ in seen] == ["text", "principal", "now"]
    assert [type(value) for _, value in seen] == [str, Principal, datetime]
    assert not isinstance(seen[1][1], HavenStore)
    assert runtime.intelligence_provider is not None


def test_runtime_requires_an_intelligence_provider() -> None:
    store = HavenStore(household_id=RESIDENT.household_id)
    with pytest.raises(ValueError):
        HavenRuntime(store=store, execution_providers=ExecutionProviderRegistry())
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        execution_providers=ExecutionProviderRegistry(),
    )
    assert isinstance(runtime.intelligence_provider, ScriptedIntelligenceProvider)


def test_interpret_requires_aware_utc_time() -> None:
    provider = ScriptedIntelligenceProvider()
    with pytest.raises(ValueError):
        provider.interpret(EXAMPLE, principal=RESIDENT, now=NOW.replace(tzinfo=None))
    provider.interpret(EXAMPLE, principal=RESIDENT, now=NOW + timedelta(seconds=1))


def test_interpret_intent_proposes_exactly_one_intent_form() -> None:
    """The fifth seam method classifies any utterance over the intent union."""

    provider = ScriptedIntelligenceProvider()
    context = _context()

    question = provider.interpret_intent("is the garage open?", context=context, principal=RESIDENT, now=NOW)
    assert isinstance(question, QueryRequest)
    assert question.text == "is the garage open?"

    draft = provider.interpret_intent(EXAMPLE, context=context, principal=RESIDENT, now=NOW)
    assert isinstance(draft, RuleDraft)
    assert draft.source_text == EXAMPLE

    no_focus = AgentContext(
        household_id=RESIDENT.household_id,
        actor_id=RESIDENT.actor_id,
        actor_role=RESIDENT.role_tier.name,
    )
    clarify = provider.interpret_intent("turn that light off", context=no_focus, principal=RESIDENT, now=NOW)
    assert isinstance(clarify, ClarificationRequest)
    assert clarify.question == "Which room do you mean?"


def test_interpret_intent_grounds_actions_in_the_context_world() -> None:
    """The AgentContext world/focus feed the interpreter, nothing else."""
    from test_intent_interpreter import _world

    provider = ScriptedIntelligenceProvider()
    focused = AgentContext(
        household_id=RESIDENT.household_id,
        actor_id=RESIDENT.actor_id,
        actor_role=RESIDENT.role_tier.name,
        room_focus="office",
    )

    proposal = provider.interpret_intent(
        "turn that light off", context=focused, principal=RESIDENT, now=NOW
    )
    assert isinstance(proposal, ActionProposal)
    assert proposal.target_selector is not None
    assert proposal.target_selector.room == "office"

    world = AgentContext(
        household_id=RESIDENT.household_id,
        actor_id=RESIDENT.actor_id,
        actor_role=RESIDENT.role_tier.name,
        world=_world(garage_open=True),
    )
    garage = provider.interpret_intent("close the garage", context=world, principal=RESIDENT, now=NOW)
    assert isinstance(garage, ActionProposal)
    assert garage.target_device_id == "garage_door"


def test_interpret_intent_requires_aware_utc_time() -> None:
    provider = ScriptedIntelligenceProvider()
    with pytest.raises(ValueError):
        provider.interpret_intent(
            "is the garage open?", context=_context(), principal=RESIDENT, now=NOW.replace(tzinfo=None)
        )
