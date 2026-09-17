"""The bridge that joins the Model Manager to the HAVEN runtime.

Doctrine: models upgrade the default provider; the default stays the floor.
`ModelIntelligenceProvider` wraps any `IntelligenceProvider` (the scripted
fixture by default) and routes each call through a LOADED model only when
the manager can resolve one with the required capability -- otherwise the
call delegates to the default, so HAVEN runs unchanged with no models at
all. The fixture is never replaced; a model is an upgrade path in front of
it.

Routing rules, per method:

- `chat` / `explain`: resolve kind=intelligence with requires={"chat"},
  restricted to models whose handle is actually LOADED; build a chat
  messages list whose system message summarizes the bounded `AgentContext`
  -- including the `WorldView` JSON when present, so a remote agent can
  answer "is the garage still open?" from bounded evidence -- and call
  `handle.chat(messages)`. The handle's reply envelope is read
  defensively: both {"text": ...} dicts and raw strings are accepted.
- `interpret` / `propose_rule`: structured intent is NOT reliably
  structured through free-text model handles, and this module refuses to
  pretend otherwise. These methods delegate to the default provider unless
  the resolved handle exposes a capability-gated
  `capability_method("structured_intent", ...)` whose payload maps onto a
  complete `RuleDraft`; any failure -- missing method, connection error,
  malformed payload -- falls back to the default. Structured intent remains
  the scripted/fallback path until a real structured-output contract
  exists; models upgrade chat/explain today.
- Every model invocation failure (connection error, unrecognized envelope)
  falls back to the default provider rather than failing the call: a model
  is an upgrade, never a single point of failure.

`with_model_bridge` is the documented wiring pattern: it builds a
`HavenRuntime` whose intelligence provider is this bridge, with no change
to `HavenRuntime` itself -- the bridge IS an IntelligenceProvider.

`bind_loaded_speech_models` is the honest, thin speech binding: for each
LOADED kind=speech model it registers the model's handle into a
`CapabilityRegistry` under the provider kind its capabilities map to
({"asr"} -> "speech_to_text", {"tts"} -> "text_to_speech",
{"wake_word"} -> "wake_word", {"vad"} -> "vad") so `find(kind=...)` sees
live models. Handles may not yet fully implement the speech protocols --
protocol-conformance shims are the next slice; this binding only makes the
loaded handle discoverable. Re-binding replaces, so the call is idempotent.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from haven.core.domain import ActionKind, AuthorityDecision, Principal, RuleDraft
from haven.intelligence.gateway import (
    AgentContext,
    AgentResponse,
    IntelligenceProvider,
    ScriptedIntelligenceProvider,
)
from haven.models.contracts import ModelKind
from haven.models.manager import ModelManager, ModelNotFoundError
from haven.models.states import ModelState
from haven.providers.capabilities import CapabilityRegistry, ProviderCapabilities

_SPEECH_CAPABILITY_TO_KIND = {
    "asr": "speech_to_text",
    "tts": "text_to_speech",
    "wake_word": "wake_word",
    "vad": "vad",
}

# Keys a structured-intent payload must carry to be trusted as a RuleDraft.
_DRAFT_REQUIRED_KEYS = (
    "draft_id",
    "household_id",
    "proposed_by",
    "source_text",
    "interpretation",
    "action_kind",
)


def _extract_text(result: Any) -> str:
    """Read a chat reply defensively: {"text": ...} envelopes and raw strings."""

    if isinstance(result, str) and result.strip():
        return result
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return text
    raise ValueError(
        f"model returned an unrecognized chat envelope ({type(result).__name__}); "
        "expected a string or a mapping with a non-empty 'text'"
    )


def _context_world_json(context: AgentContext) -> str | None:
    """The world projection as JSON, only when the context actually carries one."""

    return json.dumps(context.world.to_dict(), sort_keys=True) if context.world is not None else None


def _system_message(context: AgentContext) -> str:
    """The bounded context summary every model call receives."""

    lines = [
        f"household: {context.household_id}",
        f"actor: {context.actor_id} ({context.actor_role})",
    ]
    if context.room_focus is not None:
        lines.append(f"room focus: {context.room_focus}")
    if context.recent_lines:
        lines.append("recent conversation: " + " | ".join(context.recent_lines))
    world_json = _context_world_json(context)
    if world_json is not None:
        lines.append("observed world (bounded evidence projection): " + world_json)
    return "\n".join(lines)


def _draft_from_payload(payload: dict[str, Any]) -> RuleDraft:
    """Map a structured-intent payload onto a RuleDraft; gaps raise, never guess."""

    if not isinstance(payload, dict):
        raise ValueError("structured intent payload must be a JSON object")
    missing = [key for key in _DRAFT_REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"structured intent payload is missing keys: {sorted(missing)}")
    return RuleDraft(
        draft_id=payload["draft_id"],
        household_id=payload["household_id"],
        proposed_by=payload["proposed_by"],
        source_text=payload["source_text"],
        interpretation=payload["interpretation"],
        action_kind=(
            payload["action_kind"]
            if isinstance(payload["action_kind"], ActionKind)
            else ActionKind(payload["action_kind"])
        ),
        trigger_person_id=payload.get("trigger_person_id"),
        trigger_room_id=payload.get("trigger_room_id"),
        required_context=payload.get("required_context"),
        target_device_id=payload.get("target_device_id"),
        assumptions=tuple(payload.get("assumptions", ())),
        unresolved=tuple(payload.get("unresolved", ())),
        capability=payload.get("capability"),
    )


class ModelIntelligenceProvider:
    """Routes intelligence calls through LOADED models, falling back to a default.

    The wrapped `default` is the floor: any call the models cannot serve --
    nothing loaded, capability missing, invocation failure, unstructured
    reply -- lands there. The manager is only ever asked for a descriptor
    and its loaded handle; the provider never touches the store, the
    registry persistence, or anything executable.
    """

    def __init__(self, default: IntelligenceProvider, model_manager: ModelManager) -> None:
        if model_manager is None or not isinstance(model_manager, ModelManager):
            raise ValueError("model_manager must be a ModelManager")
        self._default = default
        self._model_manager = model_manager

    def _loaded_handle(self, *, requires: frozenset[str]):
        """The LOADED handle for kind=intelligence + capabilities, or None.

        `resolve` prefers LOADED records but can return a READY one; only a
        handle that `loaded_handle` actually returns is used -- a resolvable
        but not-yet-loaded model delegates to the default like no model at
        all. ModelNotFoundError is the normal "no model" signal, never a
        failure worth surfacing.
        """

        try:
            descriptor = self._model_manager.resolve(ModelKind.INTELLIGENCE, requires=requires)
        except ModelNotFoundError:
            return None
        return self._model_manager.loaded_handle(descriptor.id)

    def _structured_intent(self, *, context: AgentContext | None, text: str) -> RuleDraft | None:
        """A RuleDraft from a capability-gated structured-intent handle, or None.

        None means "the models cannot do this honestly" and the caller
        delegates to the default. Every failure mode -- no handle, no
        structured method, invocation error, malformed payload -- collapses
        to None so structured intent degrades to the scripted path.
        """

        handle = self._loaded_handle(requires=frozenset({"structured_intent"}))
        if handle is None:
            return None
        capability_method = getattr(handle, "capability_method", None)
        if capability_method is None:
            return None
        payload: dict[str, Any] = {"text": text}
        if context is not None:
            payload["context"] = context.to_dict()
        try:
            result = capability_method("structured_intent", "structured_intent", payload)
            return _draft_from_payload(result)
        except Exception:
            return None

    def _chat_text(self, handle: Any, context: AgentContext, message: str) -> str:
        messages = [
            {"role": "system", "content": _system_message(context)},
            {"role": "user", "content": message},
        ]
        return _extract_text(handle.chat(messages))

    def interpret(self, text: str, *, principal: Principal, now: datetime) -> RuleDraft:
        draft = self._structured_intent(context=None, text=text)
        if draft is not None:
            return draft
        return self._default.interpret(text, principal=principal, now=now)

    def chat(self, context: AgentContext, message: str) -> AgentResponse:
        handle = self._loaded_handle(requires=frozenset({"chat"}))
        if handle is not None:
            try:
                return AgentResponse(text=self._chat_text(handle, context, message))
            except Exception:
                pass  # a failed model call is not a failed conversation; the floor answers
        return self._default.chat(context, message)

    def propose_rule(self, context: AgentContext, message: str) -> RuleDraft:
        draft = self._structured_intent(context=context, text=message)
        if draft is not None:
            return draft
        return self._default.propose_rule(context, message)

    def explain(self, context: AgentContext, decision: AuthorityDecision) -> AgentResponse:
        handle = self._loaded_handle(requires=frozenset({"chat"}))
        if handle is not None:
            rendered = (
                f"Explain this authority decision to the human in plain language: "
                f"status={decision.status.value} code={decision.code.value} -- {decision.explanation}"
            )
            try:
                return AgentResponse(text=self._chat_text(handle, context, rendered))
            except Exception:
                pass
        return self._default.explain(context, decision)


def bind_loaded_speech_models(
    model_manager: ModelManager, capability_registry: CapabilityRegistry
) -> tuple[str, ...]:
    """Register every LOADED speech model's handle into a capability registry.

    Each loaded kind=speech model is registered under provider_id
    "model.<id>" in the registry kind its capabilities map to. Handles are
    registered as-is: they may not yet fully implement the speech provider
    protocols, and this binding does not shim them -- protocol-conformance
    shims are the next slice. Registration replaces by provider_id, so
    re-binding is idempotent. Returns the provider_ids bound.
    """

    if not isinstance(model_manager, ModelManager):
        raise ValueError("model_manager must be a ModelManager")
    if not isinstance(capability_registry, CapabilityRegistry):
        raise ValueError("capability_registry must be a CapabilityRegistry")
    bound: list[str] = []
    for record in model_manager.list_models(state=ModelState.LOADED):
        if record.kind is not ModelKind.SPEECH:
            continue
        handle = model_manager.loaded_handle(record.id)
        if handle is None:
            continue
        for capability, kind in _SPEECH_CAPABILITY_TO_KIND.items():
            if capability in record.descriptor.capabilities:
                provider_id = f"model.{record.id}"
                capability_registry.register(
                    handle,
                    capabilities=ProviderCapabilities(
                        provider_id=provider_id,
                        kind=kind,
                        capabilities=(capability,),
                    ),
                )
                if provider_id not in bound:
                    bound.append(provider_id)
    return tuple(bound)


def _with_model_bridge(
    cls,
    *,
    store,
    model_manager: ModelManager,
    default: IntelligenceProvider | None = None,
    **runtime_kwargs,
):
    """Build a HavenRuntime whose intelligence provider is the model bridge.

    This is the documented joining pattern for "Model Manager and HAVEN
    runtime": the runtime needs no signature change because the bridge IS
    an IntelligenceProvider. Everything the runtime normally takes
    (`home_assistant`, `execution_providers`, ...) passes through
    `runtime_kwargs`; `default` defaults to the scripted fixture, which
    remains the floor behind the models.
    """

    from haven.runtime import HavenRuntime

    floor = default if default is not None else ScriptedIntelligenceProvider()
    return cls(
        store=store,
        intelligence_provider=ModelIntelligenceProvider(floor, model_manager),
        **runtime_kwargs,
    )


def _install_model_bridge() -> None:
    """Attach `with_model_bridge` to HavenRuntime without touching runtime.py.

    Defined here (not in haven.runtime) so the runtime module stays free of
    any model-manager import; importing this bridge module is the explicit
    opt-in that installs the classmethod.
    """

    from haven.runtime import HavenRuntime

    if not hasattr(HavenRuntime, "with_model_bridge"):
        HavenRuntime.with_model_bridge = classmethod(_with_model_bridge)


_install_model_bridge()


__all__ = [
    "ModelIntelligenceProvider",
    "bind_loaded_speech_models",
]
