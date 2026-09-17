"""Scanning model roots produces candidates, never authority.

`scan_roots()` walks each root's immediate subdirectories and classifies
what it finds -- a declared `haven-model.json` when present, otherwise a
synthesized manifest when the folder matches a known layout (see
`haven.models.detect`) -- but it NEVER auto-registers: a scan cannot know
that a folder's bytes are trustworthy or that the household wants the model.
Activation is the explicit `ModelManager.register_candidate()` call, the
same way device enrollment is the explicit gate after device discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .detect import detect_folder
from .integrity import verify_files
from .manifest import ModelManifest, manifest_filename
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
    detection = detect_folder(folder)
    manifest = detection.manifest
    if manifest is None:
        return DiscoveryResult(
            path=folder,
            manifest=None,
            state=detection.state,
            problems=tuple(detection.problems),
        )
    if not (folder / manifest_filename()).is_file():
        # Synthesized manifest: nothing to hash-verify (no declared sha256);
        # the license-unknown problem rides along as a flag and the candidate
        # stays usable.
        return DiscoveryResult(
            path=folder,
            manifest=manifest,
            state=detection.state,
            problems=tuple(detection.problems),
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

    A folder without a manifest or a recognizable layout is reported as
    UNSUPPORTED with a problem naming what was looked for; nothing here
    writes to the registry or storage.
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
