"""The model bridge: WorldView projection, provider routing, and speech binding.

Doctrine under test: models upgrade the default provider; the default stays
the floor. A chat-capable LOADED model answers chat/explain (and sees the
bounded world projection in its system message); everything else -- no
model installed, a model not yet loaded, an invocation failure, structured
intent -- delegates to the scripted fixture, so HAVEN runs unchanged with
no models at all.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.core.domain import (
    AuthorityDecision,
    ChangeOrigin,
    ContextState,
    DecisionCode,
    DecisionStatus,
    DeviceState,
    EvidenceStatus,
    PresenceState,
    Principal,
    RoleTier,
    WorldSnapshot,
)
from haven.core.store import HavenStore
from haven.execution import ExecutionProviderRegistry
from haven.intelligence.gateway import AgentContext, ScriptedIntelligenceProvider, UnsupportedIntent
from haven.intelligence.worldview import WorldView
from haven.models import BackendRegistry, ModelKind, ModelManager, ModelState
from haven.models.bridge import ModelIntelligenceProvider, bind_loaded_speech_models
from haven.models.manifest import ModelManifest, manifest_filename
from haven.providers.capabilities import CapabilityRegistry
from haven.runtime import HavenRuntime

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
EXAMPLE = "When I'm working late, don't blast the bedroom lights when I walk in."
RESIDENT = Principal(actor_id="resident-1", household_id="household-1", role_tier=RoleTier.MEMBER)


class ScriptedHandle:
    """A loaded model handle whose chat reply is scripted and recorded."""

    def __init__(self, descriptor, reply="model says hello"):
        self._descriptor = descriptor
        self.reply = reply
        self.messages = []
        self.chat_calls = 0
        self.unloaded = False

    @property
    def descriptor(self):
        return self._descriptor

    def chat(self, messages, **params):
        self.chat_calls += 1
        self.messages.append(list(messages))
        return {"text": self.reply}

    def unload(self):
        self.unloaded = True


class FailingHandle(ScriptedHandle):
    def chat(self, messages, **params):
        self.chat_calls += 1
        raise RuntimeError("simulated backend failure")


class FakeBackend:
    """The loader plugin tests register; returns scripted handles."""

    def __init__(self, handle_factory=None):
        self.handles = {}
        self.handle_factory = handle_factory or (lambda descriptor: ScriptedHandle(descriptor))

    def load(self, descriptor, model_dir):
        handle = self.handle_factory(descriptor)
        self.handles[descriptor.id] = handle
        return handle


def _manager_with_backend(tmp: str, backend) -> ModelManager:
    registry = BackendRegistry()
    registry.register("fake", backend)
    return ModelManager(Path(tmp) / "root", backends=registry)


def _install_local(manager: ModelManager, parent: Path, model_id: str, *, kind, capabilities) -> None:
    manifest = ModelManifest(
        id=model_id,
        version="1.0.0",
        kind=kind,
        capabilities=frozenset(capabilities),
        architecture="fake-arch",
        backend="fake",
        files={"weights": "weights.bin"},
    )
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"stub weights")
    manifest.save(folder / manifest_filename())
    record = manager.install_local_folder(folder)
    assert record.state is ModelState.READY


def _intelligence_manager(tmp: str):
    backend = FakeBackend()
    manager = _manager_with_backend(tmp, backend)
    _install_local(manager, Path(tmp) / "models", "fake-agent", kind=ModelKind.INTELLIGENCE, capabilities={"chat"})
    return manager, backend


def _context(world=None) -> AgentContext:
    return AgentContext(
        household_id=RESIDENT.household_id,
        actor_id=RESIDENT.actor_id,
        actor_role=RESIDENT.role_tier.name,
        room_focus="garage",
        recent_lines=("did i leave it open?",),
        world=world,
    )


def _snapshot(*, captured_at=NOW, valid_until=NOW + timedelta(minutes=30)) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="snap-1",
        household_id="household-1",
        captured_at=captured_at,
        valid_until=valid_until,
        devices=(
            DeviceState(
                device_id="garage_door",
                kind="cover",
                room_id="garage",
                is_on=True,
                brightness_pct=None,
                observed_at=NOW - timedelta(minutes=2),
                source="ha",
                changed_by=ChangeOrigin.HUMAN,
                confidence=1.0,
            ),
            DeviceState(
                device_id="porch_light",
                kind="light",
                room_id="porch",
                is_on=False,
                brightness_pct=None,
                observed_at=NOW - timedelta(hours=3),
                source="ha",
                status=EvidenceStatus.STALE,
                confidence=0.9,
            ),
        ),
        presence=(
            PresenceState(
                person_id="resident-1",
                room_id="living_room",
                present=True,
                observed_at=NOW - timedelta(seconds=30),
                source="camera",
                confidence=0.7,
            ),
        ),
        contexts=(
            ContextState(
                context_id="working_late",
                active=True,
                observed_at=NOW - timedelta(minutes=10),
                source="calendar",
            ),
        ),
    )


# -- WorldView projection -----------------------------------------------------


def test_worldview_projects_freshness_confidence_and_origin() -> None:
    view = WorldView.from_snapshot(_snapshot(), now=NOW)

    assert view.household_id == "household-1"
    assert view.truncated is False
    garage = next(item for item in view.devices if item.device_id == "garage_door")
    assert garage.fresh is True
    assert garage.changed_by == "human"
    assert garage.is_on is True
    porch = next(item for item in view.devices if item.device_id == "porch_light")
    assert porch.fresh is False  # STALE status, even though observed_at is older
    person = view.presence[0]
    assert person.confidence == 0.7  # uncertainty carried verbatim, not booleanized
    assert person.fresh is True
    assert view.contexts[0].context_id == "working_late"
    assert view.contexts[0].active is True


def test_worldview_freshness_depends_on_now() -> None:
    snapshot = _snapshot()
    early = WorldView.from_snapshot(snapshot, now=NOW - timedelta(minutes=45))
    assert all(not item.fresh for item in early.devices)
    assert all(not item.fresh for item in early.presence)
    late = WorldView.from_snapshot(snapshot, now=NOW + timedelta(minutes=45))
    assert all(not item.fresh for item in late.devices)


def test_worldview_json_round_trip_is_lossless() -> None:
    view = WorldView.from_snapshot(_snapshot(), now=NOW)

    restored = WorldView.from_json(view.to_json())

    assert restored == view
    assert json.loads(restored.to_json()) == json.loads(view.to_json())


def test_worldview_truncation_flag_when_capped() -> None:
    devices = tuple(
        DeviceState(
            device_id=f"light-{index}",
            kind="light",
            room_id="kitchen",
            is_on=True,
            brightness_pct=None,
            observed_at=NOW,
            source="ha",
        )
        for index in range(201)
    )
    snapshot = WorldSnapshot(
        snapshot_id="snap-big",
        household_id="household-1",
        captured_at=NOW,
        valid_until=NOW + timedelta(minutes=30),
        devices=devices,
    )

    view = WorldView.from_snapshot(snapshot, now=NOW)

    assert view.truncated is True
    assert len(view.devices) == 200
    small = WorldView.from_snapshot(_snapshot(), now=NOW)
    assert small.truncated is False


# -- AgentContext gains the world ----------------------------------------------


def test_agent_context_carries_world_and_serializes() -> None:
    world = WorldView.from_snapshot(_snapshot(), now=NOW)
    context = _context(world=world)

    assert context.world is world
    restored = AgentContext.from_dict(context.to_dict())

    assert restored == context
    assert restored.world == world


def test_agent_context_world_must_be_a_worldview() -> None:
    with pytest.raises(ValueError):
        AgentContext(household_id="h", actor_id="a", actor_role="member", world={"not": "a view"})


# -- Bridge routing ------------------------------------------------------------


def test_chat_routes_to_loaded_model_with_world_in_system_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, backend = _intelligence_manager(tmp)
        manager.load("fake-agent")
        world = WorldView.from_snapshot(_snapshot(), now=NOW)
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)

        reply = provider.chat(_context(world=world), "is the garage still open?")

        handle = backend.handles["fake-agent"]
        assert reply.text == "model says hello"
        assert handle.chat_calls == 1
        system = handle.messages[0][0]
        assert system["role"] == "system"
        assert "garage_door" in system["content"]
        assert json.dumps(world.to_dict(), sort_keys=True) in system["content"]
        assert handle.messages[0][1] == {"role": "user", "content": "is the garage still open?"}


def test_chat_omits_world_when_context_has_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, backend = _intelligence_manager(tmp)
        manager.load("fake-agent")
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)

        provider.chat(_context(), EXAMPLE)

        system = backend.handles["fake-agent"].messages[0][0]
        assert "observed world" not in system["content"]


def test_chat_falls_back_to_default_when_no_model_loaded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, _ = _intelligence_manager(tmp)  # READY, never load()ed
        default = ScriptedIntelligenceProvider()
        provider = ModelIntelligenceProvider(default, manager)

        reply = provider.chat(_context(), EXAMPLE)

        assert reply.text == default.chat(_context(), EXAMPLE).text


def test_chat_falls_back_to_default_when_manager_is_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager_with_backend(tmp, FakeBackend())
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)

        with pytest.raises(UnsupportedIntent):
            provider.chat(_context(), "play some jazz")


def test_chat_falls_back_to_default_when_model_call_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend(handle_factory=lambda descriptor: FailingHandle(descriptor))
        manager = _manager_with_backend(tmp, backend)
        _install_local(manager, Path(tmp) / "models", "fake-agent", kind=ModelKind.INTELLIGENCE, capabilities={"chat"})
        manager.load("fake-agent")
        default = ScriptedIntelligenceProvider()
        provider = ModelIntelligenceProvider(default, manager)

        reply = provider.chat(_context(), EXAMPLE)

        assert reply.text == default.chat(_context(), EXAMPLE).text


def test_interpret_delegates_to_default_without_structured_handle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, _ = _intelligence_manager(tmp)
        manager.load("fake-agent")
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)

        draft = provider.interpret(EXAMPLE, principal=RESIDENT, now=NOW)

        assert draft.proposed_by == RESIDENT.actor_id
        assert draft.trigger_room_id == "bedroom"
        with pytest.raises(UnsupportedIntent):
            provider.interpret("dim the kitchen lights", principal=RESIDENT, now=NOW)


def test_interpret_uses_capability_gated_structured_method_when_present() -> None:
    class StructuredHandle(ScriptedHandle):
        def capability_method(self, name, requires, payload):
            assert name == "structured_intent"
            return {
                "draft_id": "draft-model-1",
                "household_id": RESIDENT.household_id,
                "proposed_by": RESIDENT.actor_id,
                "source_text": payload["text"],
                "interpretation": "model-drafted interpretation",
                "action_kind": "turn_light_off",
                "trigger_person_id": RESIDENT.actor_id,
                "trigger_room_id": "kitchen",
                "target_device_id": "kitchen_lights",
            }

    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend(handle_factory=lambda descriptor: StructuredHandle(descriptor))
        manager = _manager_with_backend(tmp, backend)
        _install_local(
            manager,
            Path(tmp) / "models",
            "fake-structured",
            kind=ModelKind.INTELLIGENCE,
            capabilities={"chat", "structured_intent"},
        )
        manager.load("fake-structured")
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)

        draft = provider.interpret("turn off the kitchen lights", principal=RESIDENT, now=NOW)

        assert draft.draft_id == "draft-model-1"
        assert draft.interpretation == "model-drafted interpretation"


def test_propose_rule_falls_back_to_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, _ = _intelligence_manager(tmp)
        manager.load("fake-agent")
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)

        draft = provider.propose_rule(_context(), EXAMPLE)

        assert draft.proposed_by == RESIDENT.actor_id
        assert draft.unresolved  # the fixture keeps ambiguity visible


def test_explain_routes_to_chat_capable_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, backend = _intelligence_manager(tmp)
        manager.load("fake-agent")
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)
        decision = AuthorityDecision(
            status=DecisionStatus.DENY,
            code=DecisionCode.WRONG_ROLE_TIER,
            explanation="only a household owner can approve an autonomous rule",
            required_role=RoleTier.OWNER,
        )

        reply = provider.explain(_context(), decision)

        assert reply.text == "model says hello"
        user = backend.handles["fake-agent"].messages[0][1]
        assert "only a household owner can approve an autonomous rule" in user["content"]


def test_explain_falls_back_to_default_without_models() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager_with_backend(tmp, FakeBackend())
        provider = ModelIntelligenceProvider(ScriptedIntelligenceProvider(), manager)
        decision = AuthorityDecision(
            status=DecisionStatus.ALLOW,
            code=DecisionCode.ALLOWED,
            explanation="owner approval is valid for this scoped rule",
        )

        reply = provider.explain(_context(), decision)

        assert decision.explanation in reply.text


# -- Runtime wiring -------------------------------------------------------------


def test_with_model_bridge_constructs_runtime_and_proposes_through_default() -> None:
    import haven.models.bridge  # noqa: F401 -- importing the bridge installs the classmethod

    with tempfile.TemporaryDirectory() as tmp:
        manager, _ = _intelligence_manager(tmp)  # nothing LOADED: the fixture is the floor
        store = HavenStore(household_id=RESIDENT.household_id)

        runtime = HavenRuntime.with_model_bridge(
            store=store,
            model_manager=manager,
            execution_providers=ExecutionProviderRegistry(),
        )

        assert isinstance(runtime.intelligence_provider, ModelIntelligenceProvider)
        rule = runtime.propose_from_text(EXAMPLE, principal=RESIDENT, now=NOW)
        assert rule.draft.proposed_by == RESIDENT.actor_id


# -- Speech binding --------------------------------------------------------------


def test_bind_loaded_speech_models_registers_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        backend = FakeBackend()
        manager = _manager_with_backend(tmp, backend)
        _install_local(manager, Path(tmp) / "models", "fake-asr", kind=ModelKind.SPEECH, capabilities={"asr"})
        registry = CapabilityRegistry()

        assert bind_loaded_speech_models(manager, registry) == ()  # installed but not LOADED

        manager.load("fake-asr")
        bound = bind_loaded_speech_models(manager, registry)

        assert bound == ("model.fake-asr",)
        assert registry.find(kind="speech_to_text") == ("model.fake-asr",)
        assert registry.get("model.fake-asr") is backend.handles["fake-asr"]

        again = bind_loaded_speech_models(manager, registry)
        assert again == ("model.fake-asr",)
        assert len(registry.registered()) == 1  # re-binding replaces, never duplicates
