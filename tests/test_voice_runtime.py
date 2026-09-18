"""voice_runtime: resolving assigned wake/asr/tts models into SpeechShims.

Pure `ModelManager` logic, no hardware -- mirrors `test_models_bridge.py`'s
fake-backend fixture pattern so a real install/assign/load cycle exercises
`resolve_voice_shims` exactly as `build_speech_service` will call it, without
ever touching `haven.speech.native_audio` (that boundary is already covered,
against real hardware, by `test_speech_native_audio.py`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from haven.models import BackendRegistry, ModelKind, ModelManager, ModelState
from haven.models.manager import ROLE_ASR, ROLE_TTS, ROLE_WAKE_WORD
from haven.models.manifest import ModelManifest, manifest_filename
from haven.speech.shims import SpeechShims
from haven.web.voice_runtime import resolve_voice_shims


class FakeSpeechHandle:
    """A loaded model handle whose capability responses are scripted."""

    def __init__(self, responses: dict) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def capability_method(self, method: str, requires=None, payload=None):
        self.calls.append((method, payload))
        response = self._responses[method]
        return response(payload) if callable(response) else response


class FakeBackend:
    def __init__(self, handle_factory) -> None:
        self.handle_factory = handle_factory
        self.handles: dict[str, FakeSpeechHandle] = {}

    def load(self, descriptor, model_dir):
        handle = self.handle_factory(descriptor)
        self.handles[descriptor.id] = handle
        return handle


def _manager(tmp: str, handle_factory) -> ModelManager:
    registry = BackendRegistry()
    registry.register("fake", FakeBackend(handle_factory))
    return ModelManager(Path(tmp) / "root", backends=registry)


def _install(manager: ModelManager, parent: Path, model_id: str, *, capability: str) -> None:
    manifest = ModelManifest(
        id=model_id,
        version="1.0.0",
        kind=ModelKind.SPEECH,
        capabilities=frozenset({capability}),
        architecture="fake-arch",
        backend="fake",
        files={"weights": "weights.bin"},
    )
    folder = parent / model_id
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"stub weights")
    manifest.save(folder / manifest_filename())
    record = manager.install_local_folder(folder)
    assert record.state is ModelState.READY


def _wake_handle(descriptor) -> FakeSpeechHandle:
    return FakeSpeechHandle({"wake_score": 1.0})


def _asr_handle(descriptor) -> FakeSpeechHandle:
    return FakeSpeechHandle({"transcribe": {"text": "turn off the office light"}})


def _tts_handle(descriptor) -> FakeSpeechHandle:
    return FakeSpeechHandle({"tts": {"chunks_base16": ["00" * 800]}})


def test_no_models_resolves_to_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(tmp, lambda d: FakeSpeechHandle({}))
        assert resolve_voice_shims(manager) is None


def test_wake_without_asr_resolves_to_none() -> None:
    """A wake model with no paired ASR model must not be wired: SpeechService
    would crash its own pump on the first detected utterance."""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _manager(tmp, _wake_handle)
        _install(manager, tmp_path / "models", "fake-wake", capability="wake_word")
        manager.load("fake-wake")
        manager.assign(ROLE_WAKE_WORD, "fake-wake")

        assert resolve_voice_shims(manager) is None


def test_asr_without_wake_resolves_to_none() -> None:
    """An ASR model with no wake model has nothing to trigger it either --
    HAVEN has no push-to-talk caller wired to this service yet."""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _manager(tmp, _asr_handle)
        _install(manager, tmp_path / "models", "fake-asr", capability="asr")
        manager.load("fake-asr")
        manager.assign(ROLE_ASR, "fake-asr")

        assert resolve_voice_shims(manager) is None


def test_wake_and_asr_together_resolve_a_full_shim_set() -> None:
    def handle_factory(descriptor):
        if "wake" in descriptor.id:
            return _wake_handle(descriptor)
        if "asr" in descriptor.id:
            return _asr_handle(descriptor)
        return _tts_handle(descriptor)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _manager(tmp, handle_factory)
        models_dir = tmp_path / "models"
        for model_id, capability, role in (
            ("fake-wake", "wake_word", ROLE_WAKE_WORD),
            ("fake-asr", "asr", ROLE_ASR),
            ("fake-tts", "tts", ROLE_TTS),
        ):
            _install(manager, models_dir, model_id, capability=capability)
            manager.load(model_id)
            manager.assign(role, model_id)

        shims = resolve_voice_shims(manager)
        assert isinstance(shims, SpeechShims)
        assert shims.wake is not None
        assert shims.asr is not None
        assert shims.tts is not None

        # End-to-end through the shims themselves: a wake score of 1.0 plus a
        # scripted transcript is the same "wake -> final transcript" contract
        # `HavenApplication._on_real_utterance` routes into `_chat_intent`.
        finals = shims.asr.finalize()  # nothing buffered yet
        assert finals == []
        shims.asr.accept(b"\x00" * 800)
        (final,) = shims.asr.finalize()
        assert final.text == "turn off the office light"


def test_wake_and_asr_without_tts_still_resolves() -> None:
    def handle_factory(descriptor):
        return _wake_handle(descriptor) if "wake" in descriptor.id else _asr_handle(descriptor)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manager = _manager(tmp, handle_factory)
        models_dir = tmp_path / "models"
        for model_id, capability, role in (
            ("fake-wake", "wake_word", ROLE_WAKE_WORD),
            ("fake-asr", "asr", ROLE_ASR),
        ):
            _install(manager, models_dir, model_id, capability=capability)
            manager.load(model_id)
            manager.assign(role, model_id)

        shims = resolve_voice_shims(manager)
        assert shims is not None
        assert shims.wake is not None
        assert shims.asr is not None
        assert shims.tts is None
