"""The HAVEN-KWS artifact contract: manifest validation and the
thresholded rolling-window detector, including an end-to-end run whose
scorer is backed by the stubbed inference endpoint -- proof that a real
artifact served over the HTTP seam plugs into the detector unchanged.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haven.speech.events import BYTES_PER_SAMPLE, FRAME_BYTES, WakeEvent
from haven.speech.wake_model import KwsModelManifest, ThresholdedWakeDetector


def _manifest(**overrides) -> KwsModelManifest:
    values = {"model_id": "haven-kws", "version": "1.0.0"}
    values.update(overrides)
    return KwsModelManifest(**values)


def _feed(detector, frames: int, *, frame_bytes: int = FRAME_BYTES) -> list[WakeEvent]:
    events = []
    for _ in range(frames):
        events.extend(detector.process(b"\x07\x00" * (frame_bytes // 2)))
    return events


def test_manifest_defaults_describe_a_standard_16khz_artifact() -> None:
    manifest = _manifest()

    assert manifest.sample_rate_hz == 16000
    assert manifest.frame_ms == 25
    assert manifest.window_ms == 1000
    assert manifest.threshold == 0.5
    assert manifest.debounce_ms == 750
    assert manifest.positive_phrase == "haven"
    assert manifest.frame_bytes == FRAME_BYTES
    assert manifest.window_bytes == 16000 * BYTES_PER_SAMPLE


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_id", ""),
        ("version", " "),
        ("version", ""),
        ("sample_rate_hz", 0),
        ("sample_rate_hz", True),
        ("frame_ms", 0),
        ("window_ms", 10),  # smaller than frame_ms
        ("threshold", 1.5),
        ("threshold", -0.1),
        ("threshold", True),
        ("threshold", "high"),
        ("debounce_ms", -1),
        ("positive_phrase", ""),
    ],
)
def test_manifest_validation_rejects_bad_contracts(field, value) -> None:
    with pytest.raises(ValueError):
        _manifest(**{field: value})


def test_scorer_must_be_callable_and_clock_must_be_callable() -> None:
    with pytest.raises(ValueError, match="scorer"):
        ThresholdedWakeDetector(_manifest(), "not-callable")
    with pytest.raises(ValueError, match="clock_ms"):
        ThresholdedWakeDetector(_manifest(), lambda window: 0.0, clock_ms=42)


def test_scorer_must_return_a_number() -> None:
    detector = ThresholdedWakeDetector(_manifest(window_ms=25), lambda window: "loud")
    with pytest.raises(ValueError, match="scorer"):
        _feed(detector, 1)


def test_single_fire_per_sustained_activation() -> None:
    detector = ThresholdedWakeDetector(_manifest(), lambda window: 0.95)

    events = _feed(detector, 40)  # one full second of confident audio

    assert len(events) == 1
    assert events[0] == WakeEvent(confidence=0.95, at_ms=1000, phrase="haven")
    # Scoring starts only once the 1000 ms window has filled at frame 40;
    # that first score crosses the threshold and the sustained activation
    # after it is debounced.


def test_rearm_requires_the_score_to_drop_below_threshold() -> None:
    scores = iter([0.9] * 3 + [0.1] * 2 + [0.9] * 3)
    detector = ThresholdedWakeDetector(_manifest(window_ms=25, debounce_ms=0), lambda window: next(scores))

    events = _feed(detector, 8)

    assert [event.at_ms for event in events] == [25, 150]
    assert all(event.phrase == "haven" for event in events)


def test_debounce_window_suppresses_a_quick_second_fire() -> None:
    # Each frame below asks the clock once (twice on a firing frame). The
    # fire at frame 1 stamps clock 200; re-arm happens at frame 4; frames
    # 5-8 cross again at clocks 400/600/800/1000, and only 1000 is past the
    # 750 ms debounce, so exactly one refire lands.
    clock = iter([0, 200, 400, 600, 800, 1000, 1000, 1200])
    scores_by_frame = {1: 0.9, 2: 0.9, 3: 0.9, 4: 0.1, 5: 0.9, 6: 0.9, 7: 0.9, 8: 0.9}
    seen = {"frame": 0}

    def scorer(window: bytes) -> float:
        return scores_by_frame[seen["frame"]]

    detector = ThresholdedWakeDetector(
        _manifest(window_ms=25, debounce_ms=750),
        scorer,
        clock_ms=lambda: next(clock),
    )

    events = []
    for _ in range(8):
        seen["frame"] += 1
        events.extend(detector.process(b"\x07\x00" * (FRAME_BYTES // 2)))

    assert [event.at_ms for event in events] == [25, 200]


def test_rolling_window_size_comes_from_the_manifest() -> None:
    manifest = _manifest(window_ms=100)
    windows = []
    detector = ThresholdedWakeDetector(manifest, lambda window: windows.append(len(window)) or 0.1)

    _feed(detector, 10)

    # The 100 ms window is 4 frames of 25 ms; scoring starts at frame 4,
    # and every scored window is exactly window_bytes long.
    assert windows == [3200] * 7


def test_scoring_starts_only_once_the_window_is_full() -> None:
    lengths = []
    manifest = _manifest(window_ms=100)  # window_bytes == 4 frames
    detector = ThresholdedWakeDetector(manifest, lambda window: lengths.append(len(window)) or 0.1)

    _feed(detector, 3)  # 75 ms of audio: the window is not full yet
    assert lengths == []  # the scorer was never called with an undersized window

    _feed(detector, 3)  # past the window

    assert lengths == [manifest.window_bytes] * 3


def test_partial_frames_wait_for_a_full_frame() -> None:
    seen = []
    detector = ThresholdedWakeDetector(_manifest(window_ms=25), lambda window: seen.append(len(window)) or 0.9)

    assert detector.process(b"\x07\x00" * 200) == []  # half a frame
    assert seen == []
    events = detector.process(b"\x07\x00" * 200)  # completes the frame
    assert len(events) == 1
    assert seen == [FRAME_BYTES]


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.requests.append(body)
        scores = self.server.scores
        payload = {"score": scores[min(len(self.server.requests), len(scores)) - 1], "phrase": "haven"}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def scoring_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.requests = []
    server.scores = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_scorer_backed_by_the_inference_endpoint_plugs_in_unchanged(scoring_server) -> None:
    """End to end: the HTTP inference seam becomes a scorer; the detector
    built on the artifact manifest does not know or care."""
    import urllib.request

    scoring_server.scores = [0.9, 0.9, 0.9, 0.1, 0.9]

    base_url = f"http://127.0.0.1:{scoring_server.server_address[1]}"

    def endpoint_scorer(window: bytes) -> float:
        envelope = json.dumps({"model_id": "haven-kws", "pcm_base16": window.hex()}).encode("utf-8")
        request = urllib.request.Request(
            base_url + "/wake/score", data=envelope, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return float(json.loads(response.read().decode("utf-8"))["score"])

    detector = ThresholdedWakeDetector(_manifest(window_ms=25, debounce_ms=0), endpoint_scorer)

    events = _feed(detector, 5)

    assert [event.at_ms for event in events] == [25, 125]
    assert len(scoring_server.requests) == 5
    first_hex = scoring_server.requests[0]["pcm_base16"]
    assert len(bytes.fromhex(first_hex)) == FRAME_BYTES  # first rolling window is one frame
