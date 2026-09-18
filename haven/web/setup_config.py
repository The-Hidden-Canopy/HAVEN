"""First-run setup config: HAVEN's own installation state, persisted as JSON.

This is deliberately separate from the demo world the web surface shows:
`haven.json` records how this HAVEN installation was set up (data dir,
provider, preferences), not what the simulated household is doing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_CONFIG_VERSION = 1

_CONFIG_KEYS = (
    "version",
    "completed",
    "data_dir",
    "household_id",
    "provider_kind",
    "provider_base_url",
    "provider_token_file",
    "voice_enabled",
    "intelligence_enabled",
)


class SetupConfigError(ValueError):
    """Raised when the setup config cannot be read or written as asked."""


def default_data_dir() -> Path:
    """`~/.haven`; pathlib home resolves HOME fallbacks portably."""

    return Path.home() / ".haven"


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write `<path>.tmp` then `os.replace`, so readers never see a partial file."""

    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise SetupConfigError(f"could not write setup config {path}: {exc}") from exc


@dataclass(frozen=True)
class SetupConfig:
    """One installation's setup state; `None` fields mean "not chosen yet".

    Token material is never stored here: `provider_token_file` is only the
    filename of a sidecar the provider step writes next to the config.

    `household_id` is `None` until this installation has a real household to
    identify (see `haven.web.application.ensure_household_id`): a pure demo
    run never mints one, but the moment a provider is configured, one is
    generated and persisted here permanently, so restarts and rebuilds always
    read back the same identity instead of a hardcoded literal every
    installation would otherwise share.
    """

    version: int = _CONFIG_VERSION
    completed: bool = False
    data_dir: str | None = None
    household_id: str | None = None
    provider_kind: str | None = None
    provider_base_url: str | None = None
    provider_token_file: str | None = None
    voice_enabled: bool = False
    intelligence_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version != _CONFIG_VERSION:
            raise ValueError(f"unsupported setup config version: {self.version!r}")
        for name in ("data_dir", "household_id", "provider_kind", "provider_base_url", "provider_token_file"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string when set")
            object.__setattr__(self, name, value.strip())
        for name in ("completed", "voice_enabled", "intelligence_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")


class SetupConfigStore:
    """Loads and atomically saves one `SetupConfig` at a JSON file path.

    The path is settable: choosing a new data dir rebinds the store to the
    moved `haven.json`, and every sidecar path derived from `path.parent`
    moves with it.
    """

    def __init__(self, config_path: str | Path) -> None:
        self.path = config_path

    @property
    def path(self) -> Path:
        return self._path

    @path.setter
    def path(self, value: str | Path) -> None:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise SetupConfigError("setup config path must be a non-empty string or path")
        self._path = Path(value)

    def load(self) -> SetupConfig:
        """A missing file means "not set up yet", not an error."""

        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return SetupConfig()
        except OSError as exc:
            raise SetupConfigError(f"could not read setup config {self.path}: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise SetupConfigError(f"setup config is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict):
            raise SetupConfigError("setup config must be a JSON object")
        unknown = sorted(set(data) - set(_CONFIG_KEYS))
        if unknown:
            raise SetupConfigError(f"unknown setup config keys: {unknown}")
        version = data.get("version", _CONFIG_VERSION)
        if version != _CONFIG_VERSION:
            raise SetupConfigError(f"unsupported setup config version: {version!r}")
        for name in ("completed", "voice_enabled", "intelligence_enabled"):
            value = data.get(name, False)
            if not isinstance(value, bool):
                raise SetupConfigError(f"setup config field {name!r} must be a boolean")
        for name in ("data_dir", "household_id", "provider_kind", "provider_base_url", "provider_token_file"):
            value = data.get(name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise SetupConfigError(
                    f"setup config field {name!r} must be a non-empty string or null"
                )
        return SetupConfig(
            version=version,
            completed=data.get("completed", False),
            data_dir=data.get("data_dir"),
            household_id=data.get("household_id"),
            provider_kind=data.get("provider_kind"),
            provider_base_url=data.get("provider_base_url"),
            provider_token_file=data.get("provider_token_file"),
            voice_enabled=data.get("voice_enabled", False),
            intelligence_enabled=data.get("intelligence_enabled", False),
        )

    def save(self, config: SetupConfig) -> None:
        _write_json_atomic(
            self.path,
            {
                "version": config.version,
                "completed": config.completed,
                "data_dir": config.data_dir,
                "household_id": config.household_id,
                "provider_kind": config.provider_kind,
                "provider_base_url": config.provider_base_url,
                "provider_token_file": config.provider_token_file,
                "voice_enabled": config.voice_enabled,
                "intelligence_enabled": config.intelligence_enabled,
            },
        )


__all__ = [
    "SetupConfig",
    "SetupConfigError",
    "SetupConfigStore",
    "_CONFIG_VERSION",
    "default_data_dir",
]
