"""`PiperLoadedModel` resampling: a "medium"/"high" Piper voice (typically
22050 Hz) must come out at HAVEN's pinned 16 kHz wire contract just as
cleanly as a "low" voice (already 16000 Hz, no resampling needed). The
subprocess call to the real `piper.exe` is stubbed throughout -- these
tests are about the resample step around it, not the executable itself
(that is exercised for real in `scripts/install_piper.py` + manual
verification, not in the suite).
"""

from __future__ import annotations

import array
import json
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from haven.models.backends.piper_native import (
    PiperLoadedModel,
    _read_sample_rate,
    _resample_pcm16_mono,
)
from haven.models.contracts import ModelDescriptor, ModelKind, ModelSource


def _tone_pcm(*, sample_rate: int, seconds: float = 0.5, amplitude: int = 10000) -> bytes:
    n = int(sample_rate * seconds)
    samples = array.array("h", [amplitude if i % 2 == 0 else -amplitude for i in range(n)])
    return samples.tobytes()


def _descriptor() -> ModelDescriptor:
    return ModelDescriptor(
        id="piper-test-voice",
        kind=ModelKind.SPEECH,
        capabilities=frozenset({"tts"}),
        backend="piper",
        architecture="piper",
        version="1.0.0",
        source=ModelSource.LOCAL,
        files={"model": "voice.onnx", "config": "voice.onnx.json"},
    )


def _write_config(tmp: Path, sample_rate: int) -> Path:
    config_path = tmp / "voice.onnx.json"
    config_path.write_text(json.dumps({"audio": {"sample_rate": sample_rate}}), encoding="utf-8")
    return config_path


class _StubResult(SimpleNamespace):
    pass


def test_resample_identity_when_rates_already_match() -> None:
    pcm = _tone_pcm(sample_rate=16000)
    assert _resample_pcm16_mono(pcm, 16000, 16000) == pcm


def test_resample_downsamples_to_the_target_length() -> None:
    pcm = _tone_pcm(sample_rate=22050, seconds=1.0)
    resampled = _resample_pcm16_mono(pcm, 22050, 16000)
    n_out_samples = len(resampled) // 2
    # Exact rounding target: round(n_in * to_rate / from_rate).
    n_in_samples = len(pcm) // 2
    expected = round(n_in_samples * 16000 / 22050)
    assert n_out_samples == expected


def test_resample_upsamples_to_the_target_length() -> None:
    pcm = _tone_pcm(sample_rate=8000, seconds=1.0)
    resampled = _resample_pcm16_mono(pcm, 8000, 16000)
    assert len(resampled) // 2 == 16000


def test_resample_handles_a_trailing_odd_byte() -> None:
    pcm = _tone_pcm(sample_rate=16000) + b"\x01"
    # Must not raise on the unpaired trailing byte, and must not grow it
    # into a spurious extra sample.
    resampled = _resample_pcm16_mono(pcm, 16000, 8000)
    assert len(resampled) % 2 == 0


def test_read_sample_rate_from_a_real_config_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write_config(Path(tmp), 22050)
        assert _read_sample_rate(config_path) == 22050


def test_read_sample_rate_is_none_without_a_config_path() -> None:
    assert _read_sample_rate(None) is None


def test_read_sample_rate_is_none_for_a_missing_file() -> None:
    assert _read_sample_rate(Path("does/not/exist.json")) is None


def test_read_sample_rate_is_none_for_malformed_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "voice.onnx.json"
        config_path.write_text("not json", encoding="utf-8")
        assert _read_sample_rate(config_path) is None


def test_synthesize_resamples_a_medium_quality_voice_to_the_wire_contract(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = _write_config(tmp_path, 22050)
        model_path = tmp_path / "voice.onnx"
        model_path.write_bytes(b"")

        raw_pcm = _tone_pcm(sample_rate=22050, seconds=0.5)

        def fake_run(args, **kwargs):
            return _StubResult(returncode=0, stdout=raw_pcm, stderr=b"")

        monkeypatch.setattr("haven.models.backends.piper_native.subprocess.run", fake_run)

        model = PiperLoadedModel(
            _descriptor(), executable=tmp_path / "piper.exe", model_path=model_path, config_path=config_path
        )
        result = model.capability_method("tts", ["tts"], {"text": "hello"})
        pcm = b"".join(bytes.fromhex(c) for c in result["chunks_base16"])
        expected_samples = round((len(raw_pcm) // 2) * 16000 / 22050)
        assert len(pcm) // 2 == expected_samples


def test_synthesize_leaves_a_16khz_voice_untouched(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = _write_config(tmp_path, 16000)
        model_path = tmp_path / "voice.onnx"
        model_path.write_bytes(b"")

        raw_pcm = _tone_pcm(sample_rate=16000, seconds=0.5)

        def fake_run(args, **kwargs):
            return _StubResult(returncode=0, stdout=raw_pcm, stderr=b"")

        monkeypatch.setattr("haven.models.backends.piper_native.subprocess.run", fake_run)

        model = PiperLoadedModel(
            _descriptor(), executable=tmp_path / "piper.exe", model_path=model_path, config_path=config_path
        )
        result = model.capability_method("tts", ["tts"], {"text": "hello"})
        pcm = b"".join(bytes.fromhex(c) for c in result["chunks_base16"])
        assert pcm == raw_pcm


def test_synthesize_without_a_config_path_skips_resampling(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        model_path = tmp_path / "voice.onnx"
        model_path.write_bytes(b"")

        raw_pcm = _tone_pcm(sample_rate=22050, seconds=0.5)

        def fake_run(args, **kwargs):
            return _StubResult(returncode=0, stdout=raw_pcm, stderr=b"")

        monkeypatch.setattr("haven.models.backends.piper_native.subprocess.run", fake_run)

        model = PiperLoadedModel(
            _descriptor(), executable=tmp_path / "piper.exe", model_path=model_path, config_path=None
        )
        result = model.capability_method("tts", ["tts"], {"text": "hello"})
        pcm = b"".join(bytes.fromhex(c) for c in result["chunks_base16"])
        assert pcm == raw_pcm
