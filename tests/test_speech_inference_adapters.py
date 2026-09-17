"""The inference adapters over a stubbed HTTP inference endpoint.

A stdlib `http.server` stands in for the household's inference stack on a
port-0 socket in a thread, serving scripted responses and recording every
request. The tests pin the wire contract: how audio is batched before it
is scored, how scores become debounced wake events, how ASR partials and
finals map to the event dataclasses, and that a dead endpoint surfaces as
`InferenceUnavailableError` (evidence-unavailable), never as silence that
could be mistaken for a transcript.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haven.speech.events import FRAME_BYTES, TranscriptFinal, TranscriptPartial, WakeEvent
from haven.speech.providers import (
    InferenceAsrProvider,
    InferenceEndpointConfig,
    InferenceSynthesizer,
    InferenceUnavailableError,
    InferenceWakeDetector,
)

MODEL_ID = "haven-kws-demo"


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        self.server.requests.append(
            {"path": self.path, "body": body, "authorization": self.headers.get("Authorization")}
        )
        responder = self.server.responders.get(self.path)
        if responder is None:
            self._reply(404, {"error": f"unknown path {self.path}"})
            return
        try:
            payload = responder(body)
        except Exception as exc:  # the stub simulates a broken endpoint
            self._reply(500, {"error": str(exc)})
            return
        self._reply(200, payload)

    def _reply(self, status: int, payload) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def inference_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.requests = []
    server.responders = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _config(server, **overrides) -> InferenceEndpointConfig:
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    values = {"base_url": base_url, "model_id": MODEL_ID}
    values.update(overrides)
    return InferenceEndpointConfig(**values)


def _feed_frames(detector, count: int, frame: bytes = b"\x01\x00" * 400) -> list[WakeEvent]:
    events = []
    for _ in range(count):
        events.extend(detector.process(frame))
    return events


def test_config_requires_endpoint_and_model() -> None:
    with pytest.raises(ValueError, match="base_url"):
        InferenceEndpointConfig(base_url=" ", model_id=MODEL_ID)
    with pytest.raises(ValueError, match="model_id"):
        InferenceEndpointConfig(base_url="http://127.0.0.1:1", model_id="")
    with pytest.raises(ValueError, match="timeout_seconds"):
        InferenceEndpointConfig(base_url="http://127.0.0.1:1", model_id=MODEL_ID, timeout_seconds=0)
    with pytest.raises(ValueError, match="api_key"):
        InferenceEndpointConfig(base_url="http://127.0.0.1:1", model_id=MODEL_ID, api_key=" ")


def test_wake_batches_frames_into_one_query_per_window(inference_server) -> None:
    inference_server.responders["/wake/score"] = lambda body: {"score": 0.1, "phrase": "haven"}
    detector = InferenceWakeDetector(_config(inference_server), query_every_ms=100, min_activations=1)

    # Eight 25 ms frames in a single process call: exactly two 100 ms windows.
    events = detector.process(b"\x01\x00" * 400 * 8)

    assert events == []
    scored = [request for request in inference_server.requests if request["path"] == "/wake/score"]
    assert len(scored) == 2
    for request in scored:
        assert request["body"]["model_id"] == MODEL_ID
        pcm = bytes.fromhex(request["body"]["pcm_base16"])
        assert len(pcm) == 4 * FRAME_BYTES  # one query window = 4 frames
    assert scored[0]["authorization"] is None


def test_wake_sends_bearer_token_only_when_configured(inference_server) -> None:
    inference_server.responders["/wake/score"] = lambda body: {"score": 0.1, "phrase": "haven"}
    detector = InferenceWakeDetector(_config(inference_server, api_key="secret"), query_every_ms=25, min_activations=1)
    detector.process(b"\x01\x00" * 400)
    assert inference_server.requests[0]["authorization"] == "Bearer secret"


def test_wake_threshold_and_debounce_fire_once_then_rearm(inference_server) -> None:
    scores = iter([0.9, 0.9, 0.9, 0.1, 0.9, 0.9])
    inference_server.responders["/wake/score"] = lambda body: {"score": next(scores), "phrase": "haven"}
    detector = InferenceWakeDetector(_config(inference_server), query_every_ms=25, min_activations=2)

    events = _feed_frames(detector, 6)

    assert [event.at_ms for event in events] == [50, 150]
    assert all(isinstance(event, WakeEvent) for event in events)
    assert events[0].confidence == pytest.approx(0.9)
    # Sustained high scores after the fire are debounced; the drop to 0.1
    # re-arms, and the fresh two-window streak fires exactly one more event.


def test_wake_min_activations_suppresses_a_single_spike(inference_server) -> None:
    scores = iter([0.95, 0.1, 0.1, 0.1])
    inference_server.responders["/wake/score"] = lambda body: {"score": next(scores), "phrase": "haven"}
    detector = InferenceWakeDetector(_config(inference_server), query_every_ms=25, min_activations=2)

    assert _feed_frames(detector, 4) == []


def test_asr_maps_partials_and_final(inference_server) -> None:
    inference_server.responders["/asr/accept"] = lambda body: {
        "partials": [{"text": "haven", "at_ms": 100}, {"text": "haven turn", "at_ms": 250}]
    }
    inference_server.responders["/asr/finalize"] = lambda body: {
        "text": "haven turn off the light",
        "start_ms": 0,
        "end_ms": 900,
        "confidence": 0.87,
    }
    asr = InferenceAsrProvider(_config(inference_server))

    partials = asr.accept(b"\x02\x00" * 400)
    assert partials == [
        TranscriptPartial(text="haven", at_ms=100),
        TranscriptPartial(text="haven turn", at_ms=250),
    ]
    accepts = [request for request in inference_server.requests if request["path"] == "/asr/accept"]
    assert accepts[0]["body"]["pcm_base16"] == (b"\x02\x00" * 400).hex()

    finals = asr.finalize()
    assert len(finals) == 1
    assert isinstance(finals[0], TranscriptFinal)
    assert finals[0].text == "haven turn off the light"
    assert finals[0].start_ms == 0
    assert finals[0].end_ms == 900
    assert finals[0].at_ms == 900
    assert finals[0].confidence == pytest.approx(0.87)


def test_asr_finalize_clamps_confidence_via_the_event_dataclass(inference_server) -> None:
    inference_server.responders["/asr/finalize"] = lambda body: {
        "text": "haven",
        "start_ms": 0,
        "end_ms": 100,
        "confidence": 1.5,
    }
    asr = InferenceAsrProvider(_config(inference_server))

    finals = asr.finalize()

    assert finals[0].confidence == 1.0


def test_tts_yields_chunks_and_stop_truncates_the_stream(inference_server) -> None:
    inference_server.responders["/tts"] = lambda body: ["aa" * FRAME_BYTES, "bb" * FRAME_BYTES, "cc" * FRAME_BYTES]
    synth = InferenceSynthesizer(_config(inference_server))

    stream = iter(synth.speak("The garage is closed."))
    request = inference_server.requests[0]
    assert request["path"] == "/tts"
    assert request["body"] == {"model_id": MODEL_ID, "text": "The garage is closed."}

    first = next(stream)
    assert first == b"\xaa" * FRAME_BYTES
    synth.stop()  # no round trip: the remaining chunks never reach playback
    assert list(stream) == []


def test_tts_requires_a_list_of_base16_chunks(inference_server) -> None:
    inference_server.responders["/tts"] = lambda body: {"chunks": ["aa"]}
    synth = InferenceSynthesizer(_config(inference_server))
    with pytest.raises(InferenceUnavailableError):
        synth.speak("hello")


def test_dead_endpoint_raises_inference_unavailable() -> None:
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    config = InferenceEndpointConfig(base_url=f"http://127.0.0.1:{port}", model_id=MODEL_ID, timeout_seconds=1.0)

    with pytest.raises(InferenceUnavailableError):
        InferenceWakeDetector(config, query_every_ms=25).process(b"\x01\x00" * 400)
    with pytest.raises(InferenceUnavailableError):
        InferenceAsrProvider(config).accept(b"\x01\x00" * 400)
    with pytest.raises(InferenceUnavailableError):
        InferenceAsrProvider(config).finalize()
    with pytest.raises(InferenceUnavailableError):
        InferenceSynthesizer(config).speak("hello")


def test_endpoint_error_status_raises_inference_unavailable(inference_server) -> None:
    def boom(body):
        raise RuntimeError("model offline")

    inference_server.responders["/wake/score"] = boom
    detector = InferenceWakeDetector(_config(inference_server), query_every_ms=25)
    with pytest.raises(InferenceUnavailableError):
        detector.process(b"\x01\x00" * 400)
