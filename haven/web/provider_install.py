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

`installed_providers.json` is the index (which providers are installed,
under which entry point, enabled or not); `provider_<id>_config.json` is
each one's own config sidecar. Losing or corrupting either degrades to
"nothing installed"/"no config" rather than failing boot, the same
resilience `setup_service.py`'s other sidecars already have.
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


def save_installed_provider(
    store: SetupConfigStore, *, provider_id: str, entry_point_name: str, config: dict
) -> None:
    """Record a provider as activated and persist its own config sidecar.

    Replaces any prior entry for the same `provider_id` (re-activating with
    new config, e.g. after a credential rotation, is the same call).
    """

    existing = {p.provider_id: p for p in load_installed_providers(store)}
    existing[provider_id] = InstalledProvider(provider_id=provider_id, entry_point_name=entry_point_name, enabled=True)
    _save_index(store, tuple(existing.values()))

    config_path = _config_path(store, provider_id)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        # Advisory even on POSIX and meaningless on Windows -- the same
        # best-effort tightening the home_assistant token file already does.
        pass


def load_installed_provider_config(store: SetupConfigStore, provider_id: str) -> dict:
    """This provider's own persisted config, or `{}` if none is on disk."""

    path = _config_path(store, provider_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
    try:
        _config_path(store, provider_id).unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "InstalledProvider",
    "find_installed_provider",
    "load_installed_provider_config",
    "load_installed_providers",
    "remove_installed_provider",
    "save_installed_provider",
    "set_installed_provider_enabled",
]
