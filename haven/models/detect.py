"""Content detection for model folders and repos without a manifest.

An ordinary HF/GGUF/ONNX/Piper/Whisper/YOLO folder has no
`haven-model.json`, so discovery alone could never classify it. This module
inspects folder contents for known layouts and SYNTHESIZES a manifest: id
slugified from the folder name, version 0.0.0, no sha256 (hash verification
unavailable -> the candidate installs with verified=False), no license (the
discovery result carries a license-unknown problem), and a `source` detail
recording `detected:<layout>`. Synthesized manifests are candidates, never
authority: detection NEVER downloads and never writes. Anything that matches
no layout is UNSUPPORTED with a problem naming what was looked for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .contracts import InvalidModelIdError, ModelKind, safe_model_id
from .manifest import ManifestError, ModelManifest, manifest_filename
from .states import ModelState

_LAYOUT_PROBLEM = (
    "no haven-model.json and no recognized layout "
    "(looked for a single *.gguf, config.json with *.safetensors/*.bin, or *.onnx)"
)


@dataclass(frozen=True)
class Detection:
    """The outcome of classifying one folder; detection never writes."""

    manifest: ModelManifest | None
    state: ModelState
    problems: tuple[str, ...] = ()
    confidence: str = "none"


def slugify_model_id(name: str) -> str:
    """Lowercase slug: runs of non-alphanumerics collapse to single dashes."""

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _architecture_from_config(config_text: str | None) -> str:
    if not config_text:
        return "unknown"
    try:
        data = json.loads(config_text)
    except json.JSONDecodeError:
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    architectures = data.get("architectures")
    if (
        isinstance(architectures, list)
        and architectures
        and isinstance(architectures[0], str)
        and architectures[0].strip()
    ):
        return architectures[0].strip()
    return "unknown"


def synthesize_from_files(
    filenames,
    *,
    name_hint: str,
    source: str | None = None,
    config_text: str | None = None,
    sizes: dict[str, int] | None = None,
) -> ModelManifest | None:
    """Build a manifest from a list of file names using known layout rules.

    Shared by folder detection (names from disk, sizes real, config.json
    content read when present) and Hugging Face repo detection (names from
    the API listing). Returns None when nothing matches a known layout.
    """

    names = sorted({Path(str(name)).name for name in filenames})
    model_id = safe_model_id(slugify_model_id(name_hint))
    ggufs = [name for name in names if name.endswith(".gguf")]
    onnx = [name for name in names if name.endswith(".onnx")]
    weights = [name for name in names if name.endswith(".safetensors") or name.endswith(".bin")]

    if len(ggufs) == 1:
        return ModelManifest(
            id=model_id,
            version="0.0.0",
            kind=ModelKind.INTELLIGENCE,
            capabilities=frozenset({"chat"}),
            architecture="gguf",
            backend="llama_cpp",
            files={"model": ggufs[0]},
            sha256={},
            license=None,
            source=source or "detected:gguf",
        )
    if "config.json" in names and weights:
        if sizes:
            chosen = max(weights, key=lambda name: sizes.get(name, 0))
        else:
            chosen = sorted(weights)[-1]
        return ModelManifest(
            id=model_id,
            version="0.0.0",
            kind=ModelKind.INTELLIGENCE,
            capabilities=frozenset({"chat"}),
            architecture=_architecture_from_config(config_text),
            backend="transformers",
            files={"model": chosen, "config": "config.json"},
            sha256={},
            license=None,
            source=source or "detected:transformers",
        )
    if len(onnx) == 1:
        lowered = onnx[0].lower()
        if "yolo" in lowered:
            kind, capabilities = ModelKind.VISION, frozenset({"object_detection"})
        elif "whisper" in lowered:
            kind, capabilities = ModelKind.SPEECH, frozenset({"asr"})
        elif "piper" in lowered or any(name == onnx[0] + ".json" for name in names):
            kind, capabilities = ModelKind.SPEECH, frozenset({"tts"})
        else:
            kind, capabilities = ModelKind.SPECIALIZED, frozenset({"inference"})
        return ModelManifest(
            id=model_id,
            version="0.0.0",
            kind=kind,
            capabilities=capabilities,
            architecture="onnx",
            backend="onnx",
            files={"model": onnx[0]},
            sha256={},
            license=None,
            source=source or "detected:onnx",
        )
    return None


def detect_folder(path: str | Path) -> Detection:
    """Classify one folder: real manifest first, known layouts second."""

    folder = Path(path)
    manifest_path = folder / manifest_filename()
    if manifest_path.is_file():
        try:
            manifest = ModelManifest.load(manifest_path)
        except (ManifestError, InvalidModelIdError) as exc:
            return Detection(
                manifest=None,
                state=ModelState.UNSUPPORTED,
                problems=(str(exc),),
            )
        return Detection(manifest=manifest, state=ModelState.INSPECTED, confidence="high")
    if not folder.is_dir():
        return Detection(
            manifest=None,
            state=ModelState.UNSUPPORTED,
            problems=(f"not a folder: {folder}",),
        )
    entries = [entry for entry in folder.iterdir() if entry.is_file()]
    config_text: str | None = None
    config_path = folder / "config.json"
    if config_path.is_file():
        try:
            config_text = config_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            config_text = None
    sizes: dict[str, int] = {}
    for entry in entries:
        try:
            sizes[entry.name] = entry.stat().st_size
        except OSError:
            sizes[entry.name] = 0
    try:
        manifest = synthesize_from_files(
            [entry.name for entry in entries],
            name_hint=folder.name,
            config_text=config_text,
            sizes=sizes,
        )
    except InvalidModelIdError as exc:
        return Detection(
            manifest=None,
            state=ModelState.UNSUPPORTED,
            problems=(f"folder name is not a usable model id: {exc}",),
        )
    if manifest is None:
        return Detection(
            manifest=None,
            state=ModelState.UNSUPPORTED,
            problems=(_LAYOUT_PROBLEM,),
        )
    return Detection(
        manifest=manifest,
        state=ModelState.INSPECTED,
        problems=("license is empty or undeclared",),
        confidence="heuristic",
    )


__all__ = [
    "Detection",
    "detect_folder",
    "slugify_model_id",
    "synthesize_from_files",
]
