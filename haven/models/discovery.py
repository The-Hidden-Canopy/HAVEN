"""Scanning model roots produces candidates, never authority.

`scan_roots()` walks each root's immediate subdirectories for a
`haven-model.json` and classifies what it finds, but it NEVER
auto-registers: a scan cannot know that a folder's bytes are trustworthy or
that the household wants the model. Activation is the explicit
`ModelManager.register_candidate()` call, the same way device enrollment is
the explicit gate after device discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .integrity import verify_files
from .manifest import ManifestError, ModelManifest, manifest_filename
from .states import ModelState


@dataclass(frozen=True)
class DiscoveryResult:
    path: Path
    manifest: ModelManifest | None
    state: ModelState
    problems: tuple[str, ...] = ()


def inspect_folder(folder: str | Path) -> DiscoveryResult:
    """Classify one candidate folder; also the unit of `scan_roots`."""

    folder = Path(folder)
    manifest_path = folder / manifest_filename()
    if not manifest_path.is_file():
        return DiscoveryResult(path=folder, manifest=None, state=ModelState.DISCOVERED)
    try:
        manifest = ModelManifest.load(manifest_path)
    except ManifestError as exc:
        return DiscoveryResult(
            path=folder,
            manifest=None,
            state=ModelState.UNSUPPORTED,
            problems=(str(exc),),
        )
    missing, mismatched = verify_files(manifest.sha256, folder)
    if missing or mismatched:
        problems = [f"declared file missing: {rel}" for rel in missing]
        problems += [f"sha256 mismatch: {rel}" for rel in mismatched]
        return DiscoveryResult(
            path=folder,
            manifest=manifest,
            state=ModelState.INCOMPLETE if missing else ModelState.HASH_MISMATCH,
            problems=tuple(problems),
        )
    if not manifest.license:
        return DiscoveryResult(
            path=folder,
            manifest=manifest,
            state=ModelState.LICENSE_UNKNOWN,
            problems=("license is empty or undeclared",),
        )
    return DiscoveryResult(path=folder, manifest=manifest, state=ModelState.INSPECTED)


def scan_roots(roots) -> list[DiscoveryResult]:
    """Classify every immediate subdirectory of each root.

    A folder without a manifest is reported as DISCOVERED (something is
    there, but it cannot be classified further); nothing here writes to
    the registry or storage.
    """

    results: list[DiscoveryResult] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            results.append(inspect_folder(child))
    return results


__all__ = ["DiscoveryResult", "inspect_folder", "scan_roots"]
