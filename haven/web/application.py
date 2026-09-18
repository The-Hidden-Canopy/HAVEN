"""The composition root: build the household from persisted setup config.

Normal boot reads the saved setup and builds the user's house when a provider
is configured; until a provider is configured (or with ``demo=True``) the
demo household is the fallback world. Agents are optional; with intelligence
disabled HAVEN runs its deterministic floor.
"""

from __future__ import annotations

from haven.core.domain import Principal, RoleTier
from haven.core.world import WorldProvider
from haven.devices import DeviceRegistry
from haven.execution import ExecutionProviderRegistry
from haven.integrations.home_assistant.client import LiveHomeAssistantAdapter
from haven.integrations.home_assistant.observer import (
    ContextSource,
    HomeAssistantObserver,
    PresenceSource,
)
from haven.integrations.home_assistant.world import HomeAssistantWorldProvider
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.models import ModelManager

from .demo import HOUSEHOLD_ID, Clock, DemoDirector
from .rules_persist import RulesPersistence
from .setup_config import SetupConfig, SetupConfigError, SetupConfigStore, _write_json_atomic
from .setup_service import (
    _ENROLLED_FILENAME,
    _HOUSEHOLD_FILENAME,
    _enrolled_v2_payload,
    HouseholdDeclarations,
    load_enrolled_sidecar,
    load_household_declarations,
)

HA_PROVIDER_ID = "home_assistant"


def build_application(
    *,
    store: SetupConfigStore,
    model_manager: ModelManager,
    clock: Clock | None = None,
    demo: bool = False,
    ha_client=None,
) -> DemoDirector:
    """Build the director for this installation.

    Decision table: ``demo=True`` always builds the demo household; otherwise
    a config that names the ``home_assistant`` provider with a readable token
    builds the real world (Home Assistant observe + execution, enrolled
    devices re-registered from the sidecar, automations restored from the
    rules sidecar, no scenario, preferences honored).
    Every other case -- no provider configured, unreadable config, missing
    token -- falls back to the demo household: the demo is the fallback world
    until a provider is configured, even when a data dir was already chosen.
    """

    try:
        config = store.load()
    except SetupConfigError:
        config = SetupConfig()
    if demo or config.provider_kind != HA_PROVIDER_ID:
        return DemoDirector(clock=clock, model_manager=model_manager)

    if not config.provider_base_url:
        # A provider kind without an endpoint cannot build a real world yet.
        return DemoDirector(clock=clock, model_manager=model_manager)
    token = _read_provider_token(store, config)
    if token is None:
        # The provider is configured but its credential cannot be read: run
        # the demo household until the token sidecar is restored.
        return DemoDirector(clock=clock, model_manager=model_manager)

    adapter = LiveHomeAssistantAdapter(base_url=config.provider_base_url, access_token=token)
    registry = DeviceRegistry()
    enrolled = load_enrolled_sidecar(store.path.parent / _ENROLLED_FILENAME)
    for manifest in enrolled.manifests:
        registry.register(manifest)
    if enrolled.migrated:
        _write_json_atomic(store.path.parent / _ENROLLED_FILENAME, _enrolled_v2_payload(enrolled.manifests))
    declarations = _load_declarations_or_empty(store)
    # Presence and context meaning are declared, not inferred: every declared
    # occupancy entity becomes one PresenceSource, every declared context one
    # ContextSource, so the observer reports people and contexts, not devices
    # alone.
    presence_sources = tuple(
        PresenceSource(entity_id=source.entity_id, person_id=person.person_id, room_id=source.room_id)
        for person in declarations.people
        for source in person.sources
    )
    context_sources = tuple(
        ContextSource(entity_id=context.entity_id, context_id=context.context_id)
        for context in declarations.contexts
    )
    world: WorldProvider = HomeAssistantWorldProvider(
        observer=HomeAssistantObserver(
            client=ha_client if ha_client is not None else adapter,
            device_registry=registry,
            household_id=HOUSEHOLD_ID,
            presence_sources=presence_sources,
            context_sources=context_sources,
        ),
        empty_household_id=HOUSEHOLD_ID,
    )
    providers = ExecutionProviderRegistry()
    providers.register(HA_PROVIDER_ID, adapter)
    # Automations survive a restart: the director rehydrates rules.json (next
    # to the setup sidecars) at construction, after its own scenario prep.
    rules_persistence = RulesPersistence(store.path.parent / "rules.json")
    # Agents are optional: with intelligence disabled HAVEN runs its
    # deterministic floor, so the scripted provider is used verbatim instead
    # of the model-bridged upgrade path.
    intelligence_provider = None if config.intelligence_enabled else ScriptedIntelligenceProvider()
    # HAVEN acts as the household's declared people: the owner is the first
    # person declared with role "owner" -- a one-person household implicitly
    # owns itself, so with no explicit owner the first declared person stands
    # in -- and the resident is the first declared person. An undeclared
    # household keeps the bootstrap identity until someone is declared, so
    # empty declarations pass no principals at all.
    resident: Principal | None = None
    owner: Principal | None = None
    if declarations.people:
        owner_person = next(
            (person for person in declarations.people if person.role == "owner"),
            declarations.people[0],
        )
        resident_person = declarations.people[0]
        resident = Principal(
            actor_id=resident_person.person_id, household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER
        )
        owner = Principal(actor_id=owner_person.person_id, household_id=HOUSEHOLD_ID, role_tier=RoleTier.OWNER)
    return DemoDirector(
        clock=clock,
        model_manager=model_manager,
        world=world,
        registry=registry,
        execution=providers,
        scenario=False,
        voice_enabled=config.voice_enabled,
        intelligence_provider=intelligence_provider,
        ha_states_source=ha_client if ha_client is not None else adapter,
        person_names={person.person_id: person.name for person in declarations.people},
        rules_persistence=rules_persistence,
        resident=resident,
        owner=owner,
    )


def _load_declarations_or_empty(store: SetupConfigStore) -> HouseholdDeclarations:
    try:
        return load_household_declarations(store.path.parent / _HOUSEHOLD_FILENAME)
    except SetupConfigError:
        # Boot must not fail on a bad declaration file: a malformed
        # household.json degrades to "nobody declared" instead of taking
        # the house down.
        return HouseholdDeclarations()


def _read_provider_token(store: SetupConfigStore, config: SetupConfig) -> str | None:
    token_file = config.provider_token_file
    if not token_file:
        return None
    try:
        token = (store.path.parent / token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


__all__ = ["HA_PROVIDER_ID", "build_application"]
