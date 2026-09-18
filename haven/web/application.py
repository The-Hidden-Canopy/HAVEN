"""The composition root: build the household from persisted setup config.

Normal boot reads the saved setup and builds the user's house when a provider
is configured; until a provider is configured (or with ``demo=True``) the
demo household is the fallback world. Agents are optional; with intelligence
disabled HAVEN runs its deterministic floor.

The demo household (``SimulatedHouse``, the garage/camera/office-light
scenario, the ``gerron`` fixture identity) is reserved for exactly those two
cases -- nothing configured yet, or an explicit ``demo=True``. Once a
household has named ``home_assistant`` as its provider, it never falls back
to that fixture again: if its connection details cannot be read right now
(a missing endpoint, an unreadable token sidecar), HAVEN still builds the
household's *real* registry, declarations, and rules, just with a world that
reports no live evidence -- the same honest "nothing observed yet" a real
provider that is merely unreachable already degrades to
(`HomeAssistantWorldProvider`), never a fictional house that looks real.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

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

from .demo import DemoDirector
from .haven_application import Clock, HavenApplication
from .history_persist import HistoryStore
from .provider_install import find_installed_provider, load_installed_provider_config
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


def _build_installed_provider_world_and_execution(
    *, store: SetupConfigStore, provider_kind: str, household_id: str
) -> tuple[WorldProvider, ExecutionProviderRegistry] | None:
    """Try to build the world/execution wiring for an activated community
    provider named as this installation's `provider_kind`.

    Returns `None` (never raises) whenever this cannot be done right now --
    nothing installed under this id, its entry point disappeared (the
    package was uninstalled), it was explicitly disabled, or `build()`
    itself failed (a bad credential, an unreachable device at construction
    time). `build_application` treats `None` exactly like "no adapter
    exists": the household's real installation with no live evidence, never
    the demo fixture and never a crashed boot.
    """

    installed = find_installed_provider(store, provider_kind)
    if installed is None or not installed.enabled:
        return None
    from haven.providers.loader import build_provider, discover_provider_packages

    discovered = next(
        (d for d in discover_provider_packages() if d.entry_point_name == installed.entry_point_name), None
    )
    if discovered is None:
        return None
    try:
        _manifest, instance = build_provider(
            discovered, config=load_installed_provider_config(store, provider_kind)
        )
    except Exception:
        # A provider package's own build() failing (bad credential, device
        # unreachable at construction, a bug in the package) must never take
        # this installation's boot down with it -- degrade to no live
        # evidence, the same as any other unreachable provider.
        return None

    providers = ExecutionProviderRegistry()
    if callable(getattr(instance, "execute", None)):
        providers.register(provider_kind, instance)
    if callable(getattr(instance, "observe", None)):
        from haven.providers.world import CompositeObserver

        world: WorldProvider = HomeAssistantWorldProvider(
            observer=CompositeObserver(providers=(instance,), household_id=household_id),
            empty_household_id=household_id,
        )
    else:
        world = HomeAssistantWorldProvider(observer=_UnconfiguredObserver(), empty_household_id=household_id)
    return world, providers


def ensure_household_id(store: SetupConfigStore, config: SetupConfig) -> tuple[SetupConfig, str]:
    """Return `config` with a real `household_id`, minting and persisting one if absent.

    Called once a household has a provider configured -- a pure demo run
    never reaches this, so it never mints an id. The mint happens exactly
    once per installation: every later boot reads the same persisted UUID
    back from `haven.json`, so rules and receipts a previous run wrote under
    it stay valid, and two separate HAVEN installations can never collide on
    the literal fixture id every one of them used to share.
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


class _UnconfiguredObserver:
    """Always fails, so `HomeAssistantWorldProvider` degrades to its honest
    empty snapshot instead of this module inventing a second "no evidence"
    representation.

    Used whenever `build_application` has no live adapter to build for the
    household's configured provider right now -- a provider this
    composition root has no adapter for at all, or `home_assistant` with
    connection details that cannot be read right now (see
    `build_application`). `HomeAssistantWorldProvider` itself is generic
    over any object satisfying `observer.observe(now=...)`; nothing here is
    HA-specific beyond this module's current provider roster.
    """

    def observe(self, *, now):  # noqa: ARG002 -- matches HomeAssistantObserver.observe
        raise RuntimeError("provider is configured but not reachable")


def build_application(
    *,
    store: SetupConfigStore,
    model_manager: ModelManager,
    clock: Clock | None = None,
    demo: bool = False,
    ha_client=None,
) -> HavenApplication:
    """Build the director for this installation.

    Decision table: ``demo=True``, or no provider named yet, builds the demo
    household (scenario, `SimulatedHouse`, the `gerron` fixture identity).
    Once a household has named ANY provider, every other case builds the
    *real* composition -- enrolled devices, declared people, persisted
    rules, preferences -- and only the world/execution wiring changes: a
    recognized provider (``home_assistant``, today) with a readable base URL
    and token gets its live adapter; a provider this composition root does
    not know how to build (a community provider not yet registered here, or
    a known one with an unreadable endpoint/token) gets a world that reports
    no live evidence rather than the demo fixture. A household that has
    connected a provider never sees a fictional house again, regardless of
    which provider it named.
    """

    try:
        config = store.load()
    except SetupConfigError:
        config = SetupConfig()
    if demo or config.provider_kind is None:
        return DemoDirector(clock=clock, model_manager=model_manager)

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
    token = _read_provider_token(store, config)
    providers = ExecutionProviderRegistry()
    if config.provider_kind == HA_PROVIDER_ID and config.provider_base_url and token is not None:
        adapter = LiveHomeAssistantAdapter(base_url=config.provider_base_url, access_token=token)
        world: WorldProvider = HomeAssistantWorldProvider(
            observer=HomeAssistantObserver(
                client=ha_client if ha_client is not None else adapter,
                device_registry=registry,
                household_id=household_id,
                presence_sources=presence_sources,
                context_sources=context_sources,
            ),
            empty_household_id=household_id,
        )
        providers.register(HA_PROVIDER_ID, adapter)
        ha_states_source = ha_client if ha_client is not None else adapter
    else:
        installed_wiring = _build_installed_provider_world_and_execution(
            store=store, provider_kind=config.provider_kind, household_id=household_id
        )
        ha_states_source = None
        if installed_wiring is not None:
            # A household activated a community provider package (see
            # `docs/authoring-providers.md` and `haven/providers/loader.py`)
            # under this exact provider_kind: its own instance supplies
            # whatever world/execution wiring it declared, through the same
            # generic seams (`ObservationProvider`, `ExecutionAdapter`) any
            # other provider satisfies.
            world, providers = installed_wiring
        else:
            # No adapter can be built for this installation right now:
            # nothing is installed under this provider_kind at all, the
            # package that was installed got uninstalled or disabled, its
            # own build() failed, or it is `home_assistant` with connection
            # details that cannot be read right now (no endpoint, or the
            # token sidecar is missing or unreadable). Either way this is
            # the household's real installation with a provider that is
            # simply not reachable right now, not an unconfigured one: its
            # enrolled devices, declared people, and rules all still apply,
            # they simply have no live evidence -- exactly how a live
            # adapter that starts failing every fetch already degrades,
            # never the demo fixture. No execution provider is registered
            # either: a command against a device with no reachable adapter
            # fails as an ordinary execution result, the same as any other
            # unregistered provider_id.
            world = HomeAssistantWorldProvider(observer=_UnconfiguredObserver(), empty_household_id=household_id)
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
