"""The reference backend set: http is real (stdlib urllib over a stub
server); the ML loaders are lazy and gate on their runtime's
importability. A loader whose runtime is absent must raise the manager's
BackendMissingError -- the BACKEND_MISSING signal, not a plain load
failure -- so these tests branch on importlib.util.find_spec and are
correct on machines with or without the optional runtimes.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from haven.models.backends import BackendRegistry
from haven.models.backends.http import HttpModelBackend, HttpLoadedModel
from haven.models.backends.llama_cpp_backend import LlamaCppModelBackend
from haven.models.backends.onnx_backend import OnnxModelBackend
from haven.models.backends.reference import BackendConnectionError, reference_backends
from haven.models.backends.transformers_backend import TransformersModelBackend
from haven.models.contracts import ModelDescriptor, ModelKind, ModelSource
from haven.models.manager import BackendMissingError


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep pytest output clean
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        self.server.requests.append({"path": self.path, "body": parsed})
        data = json.dumps({"echo_path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
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


def test_http_chat_posts_the_envelope(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    result = handle.chat([{"role": "user", "content": "hello"}], temperature=0.5)

    assert result == {"echo_path": "/chat"}
    (request,) = stub.requests
    assert request["path"] == "/chat"
    assert request["body"]["model_id"] == "remote-chat"
    assert request["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert request["body"]["temperature"] == 0.5


def test_http_complete_posts_the_prompt(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    result = handle.complete("once upon a", max_tokens=8)

    assert result == {"echo_path": "/complete"}
    (request,) = stub.requests
    assert request["path"] == "/complete"
    assert request["body"]["model_id"] == "remote-chat"
    assert request["body"]["prompt"] == "once upon a"
    assert request["body"]["max_tokens"] == 8


def test_http_capability_method_gates_on_the_descriptor(stub) -> None:
    descriptor = _endpoint_descriptor(f"http://127.0.0.1:{stub.server_address[1]}")
    handle = HttpModelBackend().load(descriptor, None)

    ok = handle.capability_method("transcribe", "asr", {"audio": "Zm9v"})
    assert ok == {"echo_path": "/transcribe"}
    assert stub.requests[-1]["body"]["audio"] == "Zm9v"
    # the named helper is the same gate
    assert handle.transcribe("YmFy") == {"echo_path": "/transcribe"}

    with pytest.raises(RuntimeError, match="tts"):
        handle.capability_method("speak", "tts", {"text": "hi"})
    with pytest.raises(RuntimeError, match="embedding_text"):
        handle.embed("hi")
    assert len(stub.requests) == 2  # gated calls never hit the wire


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


def test_reference_backends_registers_all_four() -> None:
    registry = reference_backends()
    assert isinstance(registry, BackendRegistry)
    assert registry.registered() == ("http", "transformers", "llama_cpp", "onnx")
    for name in ("http", "transformers", "llama_cpp", "onnx"):
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
