"""Reference Piper backend (lazy): a native TTS executable via subprocess.

Backends are plugins; this is a reference implementation, not a special
case, following the exact shape of the reference lazy backends
(`llama_cpp_backend.py`, `onnx_backend.py`): the module top imports nothing
beyond the stdlib, and `load()` raises the manager's `BackendMissingError`
-- never a bare ImportError/FileNotFoundError -- when the runtime isn't
present, so the manager reports the honest BACKEND_MISSING state.

Piper's runtime here is not a Python package: it is a real native
executable (piper.exe on Windows), the same "no Python ML framework
required" pattern this project already uses for a native tool it shells
out to (`haven.web.service_manager` invokes `schtasks.exe` the same way).
It is not bundled with HAVEN -- `scripts/install_piper.py` downloads the
MIT-licensed `rhasspy/piper` release (the last release of that project;
its GPL-licensed successor, piper1-gpl, is deliberately not used here) to
`~/.haven/tools/piper/`, the same way any other optional runtime is a
user's own install, never a vendored binary in this repository.

A Piper voice model (the `.onnx` weights and matching `.onnx.json` config)
is an ordinary HAVEN model, installed and versioned through the normal
Download/Load Local paths like anything else -- this backend is only the
runtime that loads one.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor

# Piper writes clean, unframed 16-bit PCM at the voice's own sample rate to
# stdout under these two flags -- no WAV header, no log lines mixed in
# (verified: --quiet leaves stderr completely empty). A voice manifest
# should declare a 16000 Hz voice (e.g. a Piper "low" quality voice) to
# match HAVEN's pinned wire contract with no resampling step.
_SYNTHESIS_TIMEOUT_SECONDS = 60
_CHUNK_BYTES = 4000
_EXECUTABLE_ENV = "HAVEN_PIPER_EXECUTABLE"


def default_piper_root() -> Path:
    """`~/.haven/tools/piper`; where `scripts/install_piper.py` extracts to."""

    return Path.home() / ".haven" / "tools" / "piper"


def piper_executable_path() -> Path:
    """The piper executable's expected location, honoring an override.

    `$HAVEN_PIPER_EXECUTABLE` names the executable directly (for a
    non-default install location); otherwise it is looked for inside
    `default_piper_root()`, matching wherever the installer script placed it.
    """

    override = os.environ.get(_EXECUTABLE_ENV)
    if override:
        return Path(override)
    exe_name = "piper.exe" if platform.system() == "Windows" else "piper"
    return default_piper_root() / exe_name


def _model_path(descriptor: ModelDescriptor, model_dir: Path | None, *, role: str) -> Path | None:
    rel = descriptor.files.get(role)
    if not rel:
        return None
    path = Path(rel)
    return path if path.is_absolute() else (model_dir or Path(".")) / path


class PiperLoadedModel:
    """A loaded Piper voice: `capability_method("tts", ...)` shells out per call.

    Stateless by design (like the reference http backend): there is no
    persistent process to hold, so `unload()` has nothing to release.
    """

    def __init__(self, descriptor: ModelDescriptor, *, executable: Path, model_path: Path, config_path: Path | None) -> None:
        self._descriptor = descriptor
        self._executable = executable
        self._model_path = model_path
        self._config_path = config_path

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def capability_method(self, name: str, requires: Any = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if name != "tts":
            raise ValueError(f"piper backend does not serve capability {name!r}")
        payload = payload or {}
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("piper 'tts' payload requires a non-empty 'text'")
        pcm = self._synthesize(text.strip())
        chunks = [pcm[i : i + _CHUNK_BYTES].hex() for i in range(0, len(pcm), _CHUNK_BYTES)]
        return {"chunks_base16": chunks}

    def _synthesize(self, text: str) -> bytes:
        args = [
            str(self._executable),
            "--model", str(self._model_path),
            "--output_raw",
            "--quiet",
        ]
        if self._config_path is not None:
            args += ["--config", str(self._config_path)]
        try:
            result = subprocess.run(
                args,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._executable.parent),
                timeout=_SYNTHESIS_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            raise RuntimeError(f"piper executable could not be run: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"piper synthesis failed (exit {result.returncode}): {detail or 'no output'}")
        return result.stdout

    def unload(self) -> None:
        pass


class PiperNativeBackend:
    """Loads a Piper voice model, only if the native executable is installed."""

    def load(self, descriptor: ModelDescriptor, model_dir: Path | None) -> PiperLoadedModel:
        executable = piper_executable_path()
        if not executable.is_file():
            from ..manager import BackendMissingError

            raise BackendMissingError(
                f"the 'piper' backend is registered but no piper executable was found at "
                f"{executable} (or ${_EXECUTABLE_ENV}); run scripts/install_piper.py to "
                f"install it before loading {descriptor.id!r}"
            )
        model_path = _model_path(descriptor, model_dir, role="model")
        if model_path is None:
            raise ValueError(
                f"piper backend requires descriptor.files['model'] to name a .onnx voice file for {descriptor.id!r}"
            )
        config_path = _model_path(descriptor, model_dir, role="config")
        return PiperLoadedModel(descriptor, executable=executable, model_path=model_path, config_path=config_path)


__all__ = [
    "PiperLoadedModel",
    "PiperNativeBackend",
    "default_piper_root",
    "piper_executable_path",
]
