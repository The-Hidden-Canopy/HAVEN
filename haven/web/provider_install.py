"""Where an installed community provider's activation state and config live.

`SetupConfig.provider_kind` already holds any provider's id, not only
`home_assistant`'s -- what was missing was somewhere to remember WHICH
`haven.providers` entry point a household activated for that id, and that
provider's own config (a bridge IP, an API key, whatever
`ProviderManifest.config_fields` declared), across restarts. This mirrors
`home_assistant`'s own token-sidecar convention (`setup_service.py`'s
`_TOKEN_FILENAME`): a provider's config never lives inline in `haven.json`,
each gets its own sidecar file next to it, and this module never inspects
what's inside that config beyond treating it as an opaque string mapping --
Haven Core does not know or care what a Philips Hue bridge IP looks like.

A field a manifest declared `secret=True` (`ProviderConfigField.secret`, an
API key or bridge password, not a bridge IP or a display name) never lands
in the same file as the rest: `save_installed_provider`'s `secret_fields`
splits the config into `provider_<id>_config.json` (everything else) and
`provider_<id>_secrets.json` (secret values only, chmod 0o600 the same
best-effort tightening the home_assistant token file already gets). Neither
of those two files' existence is meaningful on its own -- a provider with no
secret fields simply never gets one -- and `load_installed_provider_config`
merges both back into the one mapping `build()` actually needs, so no
caller has to know the split happened.

`installed_providers.json` is the index (which providers are installed,
under which entry point, enabled or not). Losing or corrupting any of these
files degrades to "nothing installed"/"no config" rather than failing boot,
the same resilience `setup_service.py`'s other sidecars already have.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .setup_config import SetupConfigStore, _write_json_atomic

_INDEX_FILENAME = "installed_providers.json"


@dataclass(frozen=True)
class InstalledProvider:
    """One activated community provider package, as this installation knows it."""

    provider_id: str
    entry_point_name: str
    enabled: bool = True


def _safe_filename_part(provider_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in provider_id)


def _index_path(store: SetupConfigStore) -> Path:
    return store.path.parent / _INDEX_FILENAME


def _config_path(store: SetupConfigStore, provider_id: str) -> Path:
    return store.path.parent / f"provider_{_safe_filename_part(provider_id)}_config.json"


def _secrets_path(store: SetupConfigStore, provider_id: str) -> Path:
    return store.path.parent / f"provider_{_safe_filename_part(provider_id)}_secrets.json"


def load_installed_providers(store: SetupConfigStore) -> tuple[InstalledProvider, ...]:
    """Every provider this installation has activated, enabled or not."""

    path = _index_path(store)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    items = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ()
    result: list[InstalledProvider] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("provider_id")
        entry_point_name = item.get("entry_point_name")
        if not isinstance(provider_id, str) or not provider_id.strip():
            continue
        if not isinstance(entry_point_name, str) or not entry_point_name.strip():
            continue
        result.append(
            InstalledProvider(
                provider_id=provider_id.strip(),
                entry_point_name=entry_point_name.strip(),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return tuple(result)


def find_installed_provider(store: SetupConfigStore, provider_id: str) -> InstalledProvider | None:
    for installed in load_installed_providers(store):
        if installed.provider_id == provider_id:
            return installed
    return None


def is_real_installation(
    store: SetupConfigStore, *, household_id: str | None, home_assistant_base_url: str | None
) -> bool:
    """Whether this is a real household that must never fall back to the
    demo fixture, regardless of whether any provider happens to be active
    right now.

    Three independent signals, checked without preferring one over another:

    - `household_id`: minted exactly once, permanently, the first time this
      installation was ever real (`ensure_household_id`), and never cleared
      afterward. This is the strongest signal precisely because it survives
      a household disconnecting Home Assistant *and* uninstalling every
      community provider -- a real household with real enrolled devices,
      declared people, and persisted rules does not stop being real just
      because its live evidence is temporarily (or permanently) gone; it
      degrades to "nothing observed yet", the same honest empty state a
      single unreachable provider already produces, never the demo fixture
      reappearing.
    - `home_assistant_base_url`: Home Assistant's own dedicated field.
    - the installed-providers index: any community provider ever activated,
      enabled or not.

    Deliberately not `SetupConfig.provider_kind is not None`: that field
    gets overwritten to whatever provider was activated *most recently*
    (`SetupService.install_provider_package`), so using it as "the"
    configured-provider signal made connecting Home Assistant and then
    installing a second provider silently stop wiring Home Assistant back
    in on the next rebuild -- a household ends up with fewer live providers
    than it configured, not more.
    """

    return (
        household_id is not None
        or home_assistant_base_url is not None
        or bool(load_installed_providers(store))
    )


def _save_index(store: SetupConfigStore, providers: tuple[InstalledProvider, ...]) -> None:
    _write_json_atomic(
        _index_path(store),
        {
            "providers": [
                {"provider_id": p.provider_id, "entry_point_name": p.entry_point_name, "enabled": p.enabled}
                for p in providers
            ]
        },
    )


def _write_sidecar(path: Path, data: dict, *, secret: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        # No fields of this kind (a provider with no secret fields, for
        # instance) -- clear any stale file from a prior activation rather
        # than persist an empty object.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.write_text(json.dumps(data), encoding="utf-8")
    if secret:
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Advisory even on POSIX and meaningless on Windows -- the same
            # best-effort tightening the home_assistant token file already does.
            pass


def save_installed_provider(
    store: SetupConfigStore,
    *,
    provider_id: str,
    entry_point_name: str,
    config: dict,
    secret_fields: frozenset[str] = frozenset(),
) -> None:
    """Record a provider as activated and persist its own config sidecars.

    Replaces any prior entry for the same `provider_id` (re-activating with
    new config, e.g. after a credential rotation, is the same call).
    `secret_fields` names which keys in `config` came from a
    `ProviderConfigField` with `secret=True` (`haven/providers/plugin.py`):
    those, and only those, are written to the separate, tightened secrets
    sidecar rather than the ordinary config file.
    """

    existing = {p.provider_id: p for p in load_installed_providers(store)}
    existing[provider_id] = InstalledProvider(provider_id=provider_id, entry_point_name=entry_point_name, enabled=True)
    _save_index(store, tuple(existing.values()))

    plain = {k: v for k, v in config.items() if k not in secret_fields}
    secret = {k: v for k, v in config.items() if k in secret_fields}
    _write_sidecar(_config_path(store, provider_id), plain, secret=False)
    _write_sidecar(_secrets_path(store, provider_id), secret, secret=True)


def load_installed_provider_config(store: SetupConfigStore, provider_id: str) -> dict:
    """This provider's own persisted config (plain + secret fields merged).

    `{}` if neither sidecar is on disk or readable.
    """

    merged: dict = {}
    for path in (_config_path(store, provider_id), _secrets_path(store, provider_id)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def set_installed_provider_enabled(store: SetupConfigStore, provider_id: str, enabled: bool) -> None:
    providers = tuple(
        InstalledProvider(provider_id=p.provider_id, entry_point_name=p.entry_point_name, enabled=enabled)
        if p.provider_id == provider_id
        else p
        for p in load_installed_providers(store)
    )
    _save_index(store, providers)


def remove_installed_provider(store: SetupConfigStore, provider_id: str) -> None:
    remaining = tuple(p for p in load_installed_providers(store) if p.provider_id != provider_id)
    _save_index(store, remaining)
    for path in (_config_path(store, provider_id), _secrets_path(store, provider_id)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "InstalledProvider",
    "find_installed_provider",
    "is_real_installation",
    "load_installed_provider_config",
    "load_installed_providers",
    "remove_installed_provider",
    "save_installed_provider",
    "set_installed_provider_enabled",
]
