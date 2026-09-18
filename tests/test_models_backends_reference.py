"""The reference backend set: http is real (stdlib urllib over a stub
server); the ML loaders are lazy and gate on their runtime's
importability. A loader whose runtime is absent must raise the manager's
BackendMissingError -- the BACKEND_MISSING signal, not a plain load
failure -- so these tests branch on importlib.util.find_spec and are
correct on machines with or without the optional runtimes.

Every chat/complete response is normalized to the canonical ChatResult
before HAVEN sees it, so the stub serves recognizable envelope shapes and
the tests assert the normalized contract; the ML backends' normalization
is exercised with fake runtime modules injected into sys.modules.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from haven.models.backends import BackendRegistry
from haven.models.backends.http import HttpModelBackend, HttpLoadedModel
from haven.models.backends.llama_cpp_backend import LlamaCppModelBackend
from haven.models.backends.onnx_backend import OnnxModelBackend
from haven.models.backends.reference import BackendConnectionError, reference_backends
from haven.models.backends.transformers_backend import TransformersModelBackend
from haven.models.contracts import ModelDescriptor, ModelKind, ModelSource
from haven.models.manager import BackendMissingError
from haven.models.results import ChatResult, InferenceResult

# The default envelope each stub route answers with; individual tests
# override entries to exercise one normalization shape at a time.
_DEFAULT_RESPONSES = {
    "/chat": {
        "choices": [{"message": {"content": "stub chat reply"}}],
        "usage": {"completion_tokens": 3},
    },
    "/complete": {"choices": [{"text": "stub completion"}]},
    "/transcribe": {"text": "stub transcript"},
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep pytest output clean
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        self.server.requests.append({"path": self.path, "body": parsed})
        response = self.server.responses.get(self.path)
        data = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.responses = dict(_DEFAULT_RESPONSES)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _endpoint_descriptor(url: str, capabilities=("chat", "asr")) -> ModelDescriptor:
    return ModelDescriptor(
        id="remote-chat",
        kind=ModelKind.INTELLIGENCE,
        capabilities=frozenset(capabilities),
        backend="http",
        architecture="remote-endpoint",
        version="1.0.0",
        source=ModelSource.ENDPOINT,
        endpoint_url=url,
    )


def _local_descriptor(backend: str, files: dict[str, str]) -> ModelDescriptor:
    return ModelDescriptor(
        id="local-model",
        kind=ModelKind.INTELLIGENCE,
        capabilities=frozenset({"chat"}),
        backend=backend,
        architecture="stub",
        version="1.0.0",
        source=ModelSource.LOCAL,
        files=files,
    )


# -- http backend: fully real over a stub server ---------------------------------


def test_http_chat_posts_the_envelope_and_returns_a_chat_result(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    result = handle.chat([{"role": "user", "content": "hello"}], temperature=0.5)

    assert isinstance(result, ChatResult)
    assert result.text == "stub chat reply"
    assert result.model_id == "remote-chat"
    assert result.usage == {"completion_tokens": 3}
    assert result.raw == _DEFAULT_RESPONSES["/chat"]
    (request,) = stub.requests
    assert request["path"] == "/chat"
    assert request["body"]["model_id"] == "remote-chat"
    assert request["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert request["body"]["temperature"] == 0.5


def test_http_complete_posts_the_prompt_and_returns_a_chat_result(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    result = handle.complete("once upon a", max_tokens=8)

    assert isinstance(result, ChatResult)
    assert result.text == "stub completion"
    assert result.model_id == "remote-chat"
    (request,) = stub.requests
    assert request["path"] == "/complete"
    assert request["body"]["model_id"] == "remote-chat"
    assert request["body"]["prompt"] == "once upon a"
    assert request["body"]["max_tokens"] == 8


@pytest.mark.parametrize(
    "response, expected_text",
    [
        ({"text": "plain text envelope"}, "plain text envelope"),
        ({"choices": [{"message": {"content": "chat-ish content"}}]}, "chat-ish content"),
        ({"choices": [{"text": "completion-ish text"}]}, "completion-ish text"),
        ("a bare json string", "a bare json string"),
    ],
)
def test_http_chat_normalizes_every_recognized_shape(stub, response, expected_text) -> None:
    stub.responses["/chat"] = response
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    result = handle.chat([{"role": "user", "content": "hello"}])

    assert isinstance(result, ChatResult)
    assert result.text == expected_text
    assert result.model_id == "remote-chat"


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "   "}}]},
        {"unexpected": "shape"},
        ["a", "list"],
        42,
        "   ",
    ],
)
def test_http_chat_rejects_unrecognized_shapes_naming_them(stub, response) -> None:
    stub.responses["/chat"] = response
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    with pytest.raises(BackendConnectionError, match="unrecognized response shape"):
        handle.chat([{"role": "user", "content": "hello"}])


def test_http_chat_non_json_body_raises_backend_connection_error() -> None:
    class _RawHandler(_Handler):
        def do_POST(self):
            self.server.requests.append({"path": self.path, "body": None})
            data = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RawHandler)
    server.requests = []
    server.responses = dict(_DEFAULT_RESPONSES)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        descriptor = _endpoint_descriptor(f"http://127.0.0.1:{server.server_address[1]}")
        handle = HttpModelBackend().load(descriptor, None)
        with pytest.raises(BackendConnectionError, match="non-JSON"):
            handle.chat([{"role": "user", "content": "hello"}])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_transcribe_and_embed_wrap_payloads_as_inference_results(stub) -> None:
    descriptor = _endpoint_descriptor(
        f"http://127.0.0.1:{stub.server_address[1]}",
        capabilities=("chat", "asr", "embedding_text"),
    )
    handle = HttpModelBackend().load(descriptor, None)

    transcript = handle.transcribe("YmFy")
    assert isinstance(transcript, InferenceResult)
    assert transcript.outputs == {"result": {"text": "stub transcript"}}
    assert transcript.raw == {"text": "stub transcript"}
    assert transcript.model_id == "remote-chat"
    assert stub.requests[-1]["body"]["audio"] == "YmFy"

    stub.responses["/embed"] = {"embedding": [0.1, 0.2]}
    embedding = handle.embed("hi")
    assert isinstance(embedding, InferenceResult)
    assert embedding.outputs == {"result": {"embedding": [0.1, 0.2]}}
    assert embedding.model_id == "remote-chat"


def test_http_capability_method_gates_on_the_descriptor(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    ok = handle.capability_method("transcribe", "asr", {"audio": "Zm9v"})
    assert ok == {"text": "stub transcript"}
    assert stub.requests[-1]["body"]["audio"] == "Zm9v"

    with pytest.raises(RuntimeError, match="tts"):
        handle.capability_method("speak", "tts", {"text": "hi"})
    with pytest.raises(RuntimeError, match="embedding_text"):
        handle.embed("hi")
    assert len(stub.requests) == 1  # gated calls never hit the wire


def test_http_unload_is_idempotent_and_stops_calls(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    handle.unload()
    handle.unload()  # double-unload is safe

    with pytest.raises(RuntimeError, match="unloaded"):
        handle.chat([{"role": "user", "content": "hello"}])


def test_http_timeout_comes_from_env(monkeypatch, stub) -> None:
    monkeypatch.setenv("HAVEN_HTTP_BACKEND_TIMEOUT", "7")
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)
    assert handle._timeout == 7.0
    monkeypatch.delenv("HAVEN_HTTP_BACKEND_TIMEOUT")
    handle = HttpModelBackend().load(descriptor, None)
    assert handle._timeout == 30.0


def test_http_rejects_a_descriptor_without_an_endpoint() -> None:
    descriptor = _local_descriptor("http", {"model": "weights.bin"})
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="http"):
            HttpModelBackend().load(descriptor, Path(tmp))


def test_http_connection_refused_raises_backend_connection_error() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # the port is now nobody's; connecting must be refused

    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{port}")
    handle = HttpModelBackend().load(descriptor, None)
    with pytest.raises(BackendConnectionError):
        handle.chat([{"role": "user", "content": "hello"}])


# -- ML backend normalization via fake runtime modules -----------------------------


class _FakeLlama:
    """Stands in for llama_cpp.Llama; answers with the native envelopes."""

    def __init__(self, model: str, n_ctx: int) -> None:
        self.model = model
        self.n_ctx = n_ctx

    def create_chat_completion(self, messages, **params):
        return {
            "choices": [{"message": {"content": "  gguf says hello  "}}],
            "usage": {"completion_tokens": 4},
        }

    def create_completion(self, prompt, **params):
        return {"choices": [{"text": " gguf completion "}]}


def test_llama_cpp_chat_and_complete_normalize_to_chat_result(monkeypatch) -> None:
    fake_runtime = SimpleNamespace(Llama=_FakeLlama)
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_runtime)
    descriptor = _local_descriptor("llama_cpp", {"model": "model.gguf"})
    with tempfile.TemporaryDirectory() as tmp:
        handle = LlamaCppModelBackend().load(descriptor, Path(tmp))

    chat = handle.chat([{"role": "user", "content": "hello"}])
    assert isinstance(chat, ChatResult)
    assert chat.text == "gguf says hello"
    assert chat.model_id == "local-model"
    assert chat.usage == {"completion_tokens": 4}

    completion = handle.complete("once upon a")
    assert isinstance(completion, ChatResult)
    assert completion.text == "gguf completion"


def test_llama_cpp_rejects_an_unrecognized_envelope(monkeypatch) -> None:
    class _WeirdLlama(_FakeLlama):
        def create_chat_completion(self, messages, **params):
            return {"unexpected": "shape"}

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=_WeirdLlama))
    descriptor = _local_descriptor("llama_cpp", {"model": "model.gguf"})
    with tempfile.TemporaryDirectory() as tmp:
        handle = LlamaCppModelBackend().load(descriptor, Path(tmp))
    with pytest.raises(ValueError, match="unrecognized chat envelope"):
        handle.chat([{"role": "user", "content": "hello"}])


class _FakeTokenIds:
    shape = (1, 4)


class _FakeTokenizer:
    def apply_chat_template(self, messages, tokenize, return_tensors, add_generation_prompt):
        return _FakeTokenIds()

    def decode(self, ids, skip_special_tokens):
        return "decoded transformers reply"


class _FakeCausalLM:
    def generate(self, ids, **params):
        return [[0, 0, 0, 0, 1, 2, 3]]


def test_transformers_chat_decodes_into_a_chat_result(monkeypatch) -> None:
    fake_transformers = SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: _FakeTokenizer()),
        AutoModelForCausalLM=SimpleNamespace(from_pretrained=lambda *a, **k: _FakeCausalLM()),
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    descriptor = _local_descriptor("transformers", {"weights": "model.safetensors"})
    with tempfile.TemporaryDirectory() as tmp:
        handle = TransformersModelBackend().load(descriptor, Path(tmp))

    result = handle.chat([{"role": "user", "content": "hello"}], max_new_tokens=3)

    assert isinstance(result, ChatResult)
    assert result.text == "decoded transformers reply"
    assert result.model_id == "local-model"


class _FakeArray:
    def __init__(self, values) -> None:
        self._values = values

    def tolist(self):
        return self._values


class _FakeSession:
    def __init__(self, path) -> None:
        self.path = path

    def run(self, outputs, feeds):
        return [_FakeArray([[0.5, 0.25]])]

    def get_outputs(self):
        return [SimpleNamespace(name="embeddings")]


def test_onnx_run_returns_an_inference_result(monkeypatch) -> None:
    fake_runtime = SimpleNamespace(InferenceSession=_FakeSession)
    fake_numpy = SimpleNamespace(asarray=lambda value: value)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_runtime)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    descriptor = _local_descriptor("onnx", {"model": "model.onnx"})
    with tempfile.TemporaryDirectory() as tmp:
        handle = OnnxModelBackend().load(descriptor, Path(tmp))

    result = handle.run({"input_ids": [[1, 2, 3]]})

    assert isinstance(result, InferenceResult)
    assert result.outputs == {"embeddings": [[0.5, 0.25]]}
    assert result.model_id == "local-model"


# -- lazy ML backends: the import gate decides the error semantics -----------------


_LAZY_BACKENDS = (
    ("transformers", TransformersModelBackend, "transformers", False),
    ("llama_cpp", LlamaCppModelBackend, "llama_cpp", True),
    ("onnx", OnnxModelBackend, "onnxruntime", True),
)


@pytest.mark.parametrize("name,loader_type,runtime,checks_files", _LAZY_BACKENDS)
def test_lazy_loader_gates_on_runtime_importability(name, loader_type, runtime, checks_files) -> None:
    loader = loader_type()
    descriptor = _local_descriptor(name, {"model": "model.bin"})
    with tempfile.TemporaryDirectory() as tmp:
        if importlib.util.find_spec(runtime) is None:
            with pytest.raises(BackendMissingError):
                loader.load(descriptor, Path(tmp))
        else:
            # runtime present: the loader must get PAST the import gate and
            # only fail later (here: on the stub weights, which do not exist)
            with pytest.raises(Exception) as excinfo:
                loader.load(descriptor, Path(tmp))
            assert not isinstance(excinfo.value, BackendMissingError)


@pytest.mark.parametrize("name,loader_type,runtime,checks_files", _LAZY_BACKENDS)
def test_lazy_loader_requires_a_model_file(name, loader_type, runtime, checks_files) -> None:
    if not checks_files:
        pytest.skip(f"the {name} backend resolves its source from descriptor.path/model_dir")
    if importlib.util.find_spec(runtime) is None:
        pytest.skip(f"{runtime} not installed here; the ValueError sits past the import gate")
    loader = loader_type()
    descriptor = _local_descriptor(name, {"weights": "weights.bin"})
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="files"):
            loader.load(descriptor, Path(tmp))


# -- the reference set and protocol conformance -----------------------------------


def test_reference_backends_registers_all_five() -> None:
    registry = reference_backends()
    assert isinstance(registry, BackendRegistry)
    assert registry.registered() == ("http", "transformers", "llama_cpp", "onnx", "piper")
    for name in ("http", "transformers", "llama_cpp", "onnx", "piper"):
        assert registry.resolve(name) is not None
    assert registry.resolve("mlx") is None  # unknown names still miss cleanly


def test_reference_backends_conform_to_the_model_backend_protocol() -> None:
    registry = reference_backends()
    for name in registry.registered():
        loader = registry.resolve(name)
        assert loader is not None
        assert callable(getattr(loader, "load", None))


def test_http_loaded_model_conforms_to_the_loaded_model_protocol(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle: HttpLoadedModel = HttpModelBackend().load(descriptor, None)
    assert handle.descriptor is descriptor
    assert callable(getattr(handle, "unload", None))
    handle.unload()
