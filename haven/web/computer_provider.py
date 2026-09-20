"""Where this installation's computer/filesystem provider config lives.

Built-in, like `home_assistant` -- not a `haven.providers` entry-point
package, so it does not go through `haven/providers/loader.py` or
`installed_providers.json`. A household explicitly names which folders
`haven.integrations.computer.FilesystemProvider` may ever touch
(`allowed_roots`); nothing here infers a default, matching the same
"no auto-discovered allowed roots" discipline that module's own docstring
states. `enabled=False` (the default) means exactly what it says -- no
scan ever runs, no folder is ever read, until a household turns this on
after choosing its own roots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .setup_config import SetupConfigStore, _write_json_atomic

_FILENAME = "computer_provider.json"


@dataclass(frozen=True)
class ComputerProviderConfig:
    enabled: bool = False
    allowed_roots: tuple[str, ...] = ()
    # Reading/indexing is the safe default.  Mutation capability must be an
    # explicit second choice in onboarding and remains subject to Authority.
    read_only: bool = True


def _config_path(store: SetupConfigStore) -> Path:
    return store.path.parent / _FILENAME


def load_computer_provider_config(store: SetupConfigStore) -> ComputerProviderConfig:
    try:
        data = json.loads(_config_path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ComputerProviderConfig()
    if not isinstance(data, dict):
        return ComputerProviderConfig()
    roots = data.get("allowed_roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
        roots = []
    return ComputerProviderConfig(
        enabled=bool(data.get("enabled", False)),
        allowed_roots=tuple(roots),
        # Older config files did not have this field.  Treating an omitted
        # value as writable would silently expand an existing installation's
        # capability on upgrade, so legacy files migrate to read-only.
        read_only=bool(data.get("read_only", True)),
    )


def save_computer_provider_config(store: SetupConfigStore, config: ComputerProviderConfig) -> None:
    _write_json_atomic(
        _config_path(store),
        {
            "enabled": config.enabled,
            "allowed_roots": list(config.allowed_roots),
            "read_only": config.read_only,
        },
    )


def build_filesystem_provider(
    config: ComputerProviderConfig,
    *,
    scope_id: str,
    open_path: Callable[[Path], None] | None = None,
    reveal_path: Callable[[Path], None] | None = None,
):
    """The real `FilesystemProvider` this config describes, or `None`.

    `None` whenever this cannot be built right now -- disabled, no roots
    named yet, or a named root that no longer exists (moved, unmounted, a
    drive letter that changed) -- matching every other provider in this
    repo: a misconfigured or unreachable provider degrades to "nothing from
    this source" rather than taking a scan or a boot down with it.
    """

    if not config.enabled or not config.allowed_roots:
        return None
    from haven.integrations.computer import FilesystemProvider

    try:
        return FilesystemProvider(
            allowed_roots=config.allowed_roots,
            scope_id=scope_id,
            read_only=config.read_only,
            open_path=open_path,
            reveal_path=reveal_path,
        )
    except ValueError:
        return None


__all__ = [
    "ComputerProviderConfig",
    "build_filesystem_provider",
    "load_computer_provider_config",
    "save_computer_provider_config",
]
