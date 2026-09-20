"""The composition root: build one HAVEN from persisted setup config.

Normal boot always builds the user's real installation. A fresh installation
with no providers yet gets a persistent identity and an empty, truthful world
whose live evidence is unavailable; it never gets fictional devices or a
fixture household. The simulated household (``SimulatedHouse``, the
garage/camera/office-light scenario, and the ``gerron`` fixture identity) is
available only through the explicit ``demo=True``/``--demo`` path. Agents are
optional; with intelligence disabled HAVEN runs its deterministic floor.

Once a household has named a provider (Home Assistant, an installed
community package, or the local computer provider), it never falls back to
the fixture again: if a provider's connection details cannot be read or its
own ``build()`` fails right now, HAVEN still builds the household's *real*
registry, declarations, and rules, just with less live evidence -- the same
honest "nothing observed yet" a real provider that is merely unreachable
already degrades to, never a fictional house that looks real.

A household's world and execution are a *composite* of every provider it has
enabled, not a single exclusive choice: Home Assistant (this repo's one
built-in integration) and any number of installed community providers
(``haven/providers/loader.py``) all contribute their own observations and
execution capability into one shared ``CompositeObserver`` /
``ExecutionProviderRegistry``, via ``build_provider_composition``. One
provider being unreachable degrades only its own contribution -- it never
takes the rest of the household's evidence or control down with it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from haven.core.domain import Principal, RoleTier
from haven.devices import DeviceRegistry
from haven.execution import ExecutionProviderRegistry
from haven.integrations.home_assistant.client import LiveHomeAssistantAdapter
from haven.integrations.home_assistant.observer import (
    ContextSource,
    HomeAssistantObserver,
    PresenceSource,
)
from haven.integrations.home_assistant.world import HomeAssistantObservationAdapter, HomeAssistantWorldProvider
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.models import ModelManager
from haven.perception.observation import ObservationProvider
from haven.providers.world import CompositeObserver

from .demo import DemoDirector
from .haven_application import Clock, HavenApplication
from .history_persist import HistoryStore
from .provider_install import load_installed_provider_config, load_installed_providers
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


@dataclass(frozen=True)
class ProviderComposition:
    """Every provider this installation has wired in, folded together.

    `ha_states_source` is kept separately (rather than only living inside
    `observation_providers`) because diagnostics' provider-reachability
    probe (`haven/web/diagnostics.py`) and `HavenApplication`'s device-state
    projection both need Home Assistant's raw client specifically, not the
    generic `Observation` shape every other source is folded down to.
    """

    observation_providers: tuple[ObservationProvider, ...]
    execution: ExecutionProviderRegistry
    ha_states_source: object | None


def build_provider_composition(
    *,
    store: SetupConfigStore,
    config: SetupConfig,
    registry: DeviceRegistry,
    household_id: str,
    presence_sources: tuple[PresenceSource, ...],
    context_sources: tuple[ContextSource, ...],
    clock: Clock,
    ha_client=None,
) -> ProviderComposition:
    """Build one composite world/execution wiring from every enabled provider.

    Home Assistant (this repo's one built-in integration, wired through its
    own dedicated `SetupConfig` fields) and every *enabled* installed
    community provider (`haven/providers/loader.py`,
    `haven/web/provider_install.py`) all contribute independently: a
    provider that cannot be built right now (unreachable, uninstalled,
    disabled, a bad credential) simply contributes nothing rather than
    taking the whole boot down or blocking every other provider from
    working. This is what lets a household run Home Assistant and a Hue
    bridge and a Matter controller all at once, none of them "the" provider.
    """

    observation_providers: list[ObservationProvider] = []
    execution = ExecutionProviderRegistry()
    ha_states_source = None

    if config.provider_base_url:
        # Home Assistant's own dedicated field is the signal for whether it
        # is configured -- never `config.provider_kind`, which a later
        # `install_provider_package()` call for an unrelated community
        # provider can overwrite (see `is_real_installation`'s docstring).
        # A household that connected Home Assistant keeps it wired in
        # regardless of what else it later installs.
        token = _read_provider_token(store, config)
        if token is not None:
            adapter = LiveHomeAssistantAdapter(base_url=config.provider_base_url, access_token=token)
            source = ha_client if ha_client is not None else adapter
            observation_providers.append(
                HomeAssistantObservationAdapter(
                    observer=HomeAssistantObserver(
                        client=source,
                        device_registry=registry,
                        household_id=household_id,
                        presence_sources=presence_sources,
                        context_sources=context_sources,
                    ),
                    clock=clock,
                )
            )
            execution.register(HA_PROVIDER_ID, adapter)
            ha_states_source = source

    from haven.providers.loader import build_provider, discover_provider_packages

    discovered_by_entry_point = {d.entry_point_name: d for d in discover_provider_packages()}
    for installed in load_installed_providers(store):
        if not installed.enabled:
            continue
        discovered = discovered_by_entry_point.get(installed.entry_point_name)
        if discovered is None:
            # The package that was activated is no longer installed in this
            # Python environment -- contributes nothing, same as any other
            # provider that can't be reached right now.
            continue
        try:
            manifest, instance = build_provider(
                discovered, config=load_installed_provider_config(store, installed.provider_id)
            )
        except Exception:
            # A provider package's own build() failing (bad credential,
            # device unreachable at construction, a bug in the package)
            # must never take this installation's boot down with it, and
            # must never block every OTHER provider from working either.
            continue
        if callable(getattr(instance, "execute", None)):
            execution.register(manifest.provider_id, instance)
        if callable(getattr(instance, "observe", None)):
            observation_providers.append(instance)

    return ProviderComposition(
        observation_providers=tuple(observation_providers), execution=execution, ha_states_source=ha_states_source
    )


def ensure_household_id(store: SetupConfigStore, config: SetupConfig) -> tuple[SetupConfig, str]:
    """Return `config` with a real `household_id`, minting and persisting one if absent.

    Normal boot calls this before composition, including a fresh installation
    with no providers. The explicit demo path never reaches it. The mint
    happens exactly once per installation: every later boot reads the same
    persisted UUID back from `haven.json`, so rules and receipts a previous
    run wrote under it stay valid, and two separate HAVEN installations can
    never collide on the literal fixture id every one of them used to share.
    """

    if config.household_id:
        return config, config.household_id
    household_id = str(uuid4())
    config = replace(config, household_id=household_id)
    try:
        store.save(config)
    except SetupConfigError:
        # Best-effort persistence: this run still gets a real, unique id even
        # if the write fails, but it will mint a fresh one again next boot
        # until the config becomes writable.
        pass
    return config, household_id


def build_application(
    *,
    store: SetupConfigStore,
    model_manager: ModelManager,
    clock: Clock | None = None,
    demo: bool = False,
    ha_client=None,
) -> HavenApplication:
    """Build the director for this installation.

    The decision is deliberately small: ``demo=True`` is the only path that
    builds the simulated household. Every normal boot, including a brand-new
    installation with no provider configured yet, builds the real composition
    -- enrolled devices, declared people, persisted rules, preferences --
    with a world and execution registry that are a *composite* of every
    provider this installation currently has enabled
    (`build_provider_composition`). A fresh install therefore has a unique
    household id and no live evidence, not fictional devices. A provider that
    cannot be built, or that a household has since disconnected or uninstalled
    entirely, degrades only its own contribution -- the installation keeps
    its real registry/declarations/rules and never becomes the demo again.
    """

    config_load_failed = False
    try:
        config = store.load()
    except SetupConfigError:
        # Keep the malformed file in place so SetupService can report the
        # configuration error and the user can repair it. A normal boot still
        # gets a real empty application, but must not silently replace a
        # broken setup file while composing that fallback.
        config = SetupConfig()
        config_load_failed = True
    if demo:
        return DemoDirector(clock=clock, model_manager=model_manager)

    resolved_clock: Clock = clock or (lambda: datetime.now(timezone.utc))
    if config_load_failed:
        household_id = str(uuid4())
    else:
        config, household_id = ensure_household_id(store, config)
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
    composition = build_provider_composition(
        store=store,
        config=config,
        registry=registry,
        household_id=household_id,
        presence_sources=presence_sources,
        context_sources=context_sources,
        clock=resolved_clock,
        ha_client=ha_client,
    )
    world = HomeAssistantWorldProvider(
        observer=CompositeObserver(providers=composition.observation_providers, household_id=household_id),
        empty_household_id=household_id,
    )
    providers = composition.execution
    ha_states_source = composition.ha_states_source
    # Automations survive a restart: the director rehydrates rules.json (next
    # to the setup sidecars) at construction, after its own scenario prep.
    rules_persistence = RulesPersistence(store.path.parent / "rules.json")
    # Events, actions, receipts, and memory survive a restart the same way,
    # in their own SQLite file: this data grows without bound over a
    # household's lifetime, unlike the small rules/enrolled-devices sidecars.
    history = HistoryStore(store.path.parent / "history.db")
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
            actor_id=resident_person.person_id, household_id=household_id, role_tier=RoleTier.MEMBER
        )
        owner = Principal(actor_id=owner_person.person_id, household_id=household_id, role_tier=RoleTier.OWNER)
    return HavenApplication(
        clock=clock,
        model_manager=model_manager,
        household_id=household_id,
        world=world,
        registry=registry,
        execution=providers,
        voice_enabled=config.voice_enabled,
        intelligence_provider=intelligence_provider,
        ha_states_source=ha_states_source,
        person_names={person.person_id: person.name for person in declarations.people},
        rules_persistence=rules_persistence,
        resident=resident,
        owner=owner,
        history=history,
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


__all__ = ["HA_PROVIDER_ID", "build_application", "ensure_household_id"]
