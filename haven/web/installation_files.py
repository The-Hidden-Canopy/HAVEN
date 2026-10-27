"""The single list of files that make up one HAVEN installation's state.

`choose_data_dir()` (moving the installation to a new root) and
`BackupManager` (copying it into a timestamped snapshot) both need the same
answer to "what files belong to this installation" -- and until this
module existed, each kept its own hardcoded tuple, so a new sidecar
(`resources.db`, `ontology.db`, `claims.db`, `computer_provider.json`,
`installed_providers.json`, and per-provider `provider_<id>_config.json`/
`provider_<id>_secrets.json` files) could ship in one and silently never
reach the other. One list, both consumers -- adding a new sidecar
(`action_ledger.db` among them) means adding it here once, not remembering
two places.

`backups/` itself is deliberately not part of this list: it is a directory
`choose_data_dir()` moves wholesale by name, and `BackupManager` obviously
does not back up its own backup directory.
"""

from __future__ import annotations

from pathlib import Path
import stat
import os

_FIXED_NAMES = (
    "haven.json",
    "ha_token.txt",  # legacy plaintext sidecar; migrated into credentials.db on first boot that reads it
    "credentials.db",
    "enrolled_devices.json",
    "household.json",
    "rules.json",
    "resource_automations.json",
    "history.db",
    "resources.db",
    "ontology.db",
    "claims.db",
    "action_ledger.db",
    "scopes.db",
    "identity.json",
    "projects.db",
    "tasks.db",
    "decisions.db",
    "features.json",
    "computer_provider.json",
    "installed_providers.json",
)

_DYNAMIC_PATTERNS = ("provider_*_config.json", "provider_*_secrets.json")
_DIRECTORY_NAMES = ("marketplace-products", "desktop", "diagnostics")


def installation_directory_names() -> tuple[str, ...]:
    return _DIRECTORY_NAMES


def _directory_files(root: Path, relative: Path):
    if relative.as_posix() == "marketplace-products/registry.lock":
        return  # OS locking is recreated locally; it is not installation state.
    candidate = root / relative
    info = candidate.lstat()
    if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("installation content contains an unsupported link")
    if candidate.is_dir():
        for child in sorted(candidate.iterdir()):
            yield from _directory_files(root, relative / child.name)
    elif candidate.is_file():
        yield relative.as_posix()


def installation_file_names(data_dir: str | Path) -> tuple[str, ...]:
    """Every file name belonging to this installation, present or not.

    The fixed set is returned unconditionally (a caller checks existence
    itself, matching how `choose_data_dir()`/`BackupManager` already skip a
    missing file rather than erroring); the per-provider config/secret
    sidecars are discovered by globbing `data_dir`, since their names are
    provider ids this module cannot enumerate in advance.
    """

    names = list(_FIXED_NAMES)
    directory = Path(data_dir).absolute()
    if os.name == "nt" and not str(directory).startswith("\\\\?\\"):
        value = str(directory)
        directory = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    if directory.is_dir():
        for pattern in _DYNAMIC_PATTERNS:
            names.extend(sorted(entry.name for entry in directory.glob(pattern)))
        for name in _DIRECTORY_NAMES:
            if (directory / name).exists():
                names.extend(_directory_files(directory, Path(name)))
    return tuple(names)


__all__ = ["installation_file_names", "installation_directory_names"]
