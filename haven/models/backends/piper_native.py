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

import array
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from ..contracts import ModelDescriptor

# Piper writes clean, unframed 16-bit PCM at the voice's own sample rate to
# stdout under these two flags -- no WAV header, no log lines mixed in
# (verified: --quiet leaves stderr completely empty). A "low" quality voice
# (16000 Hz) needs no resampling to match HAVEN's pinned wire contract; a
# "medium"/"high" voice (typically 22050 Hz) is resampled below rather than
# restricting every voice manifest to "low" -- "low" voices trade audio
# quality (audible loudness/prosody artifacts) for that exact rate match.
_SYNTHESIS_TIMEOUT_SECONDS = 60
_CHUNK_BYTES = 4000
_EXECUTABLE_ENV = "HAVEN_PIPER_EXECUTABLE"

# HAVEN's pinned wire contract (`haven.speech.events.SAMPLE_RATE_HZ`),
# repeated here rather than imported: `haven.models` is the more foundational
# package and does not depend on `haven.speech`, a consumer of it.
_TARGET_SAMPLE_RATE_HZ = 16000


def _resample_pcm16_mono(pcm: bytes, from_rate: int, to_rate: int) -> bytes:
    """Linear-interpolation resample of 16-bit mono PCM -- stdlib only.

    Good enough for spoken TTS output (no third-party DSP library needed):
    a voice assistant's synthesized speech has no content above a few kHz,
    well under either rate here, so linear interpolation introduces no
    audible artifact of its own.
    """

    if from_rate == to_rate:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    n_in = len(samples)
    if n_in < 2:
        return pcm
    n_out = max(1, round(n_in * to_rate / from_rate))
    out = array.array("h", bytes(n_out * 2))
    step = (n_in - 1) / (n_out - 1) if n_out > 1 else 0.0
    for i in range(n_out):
        pos = i * step
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < n_in:
            out[i] = int(samples[idx] + (samples[idx + 1] - samples[idx]) * frac)
        else:
            out[i] = samples[idx]
    return out.tobytes()


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


def _read_sample_rate(config_path: Path | None) -> int | None:
    """The voice's own sample rate from its `.onnx.json` config, or None.

    None means "assume Piper's stdout is already at HAVEN's target rate" --
    the config is optional on the descriptor (`_model_path` role "config"),
    and a missing/unreadable/malformed config should not turn synthesis
    into a hard failure over what was already the documented default
    before resampling existed.
    """

    if config_path is None:
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rate = data.get("audio", {}).get("sample_rate")
    return rate if isinstance(rate, int) and rate > 0 else None


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
        self._source_sample_rate = _read_sample_rate(config_path)

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
        if self._source_sample_rate is None:
            return result.stdout
        return _resample_pcm16_mono(result.stdout, self._source_sample_rate, _TARGET_SAMPLE_RATE_HZ)

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
