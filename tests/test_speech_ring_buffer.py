import pytest

from haven.speech import FRAME_BYTES, FRAME_MS, PrerollRingBuffer


def _frame(fill: int) -> bytes:
    return bytes((fill,)) * FRAME_BYTES


def test_default_capacity_is_one_second():
    buffer = PrerollRingBuffer()

    assert buffer.capacity_ms == 1000
    assert buffer.size_bytes == 0
    assert buffer.read_preroll() == b""


def test_write_and_read_back_oldest_to_newest():
    buffer = PrerollRingBuffer(capacity_ms=200)

    buffer.write(_frame(1))
    buffer.write(_frame(2))
    buffer.write(_frame(3))

    assert buffer.read_preroll() == _frame(1) + _frame(2) + _frame(3)
    assert buffer.size_bytes == 3 * FRAME_BYTES


def test_exact_capacity_is_retained_without_eviction():
    buffer = PrerollRingBuffer(capacity_ms=8 * FRAME_MS)

    payload = b"".join(_frame(i) for i in range(8))
    buffer.write(payload)

    assert buffer.size_bytes == 8 * FRAME_BYTES
    assert buffer.read_preroll() == payload


def test_overflow_evicts_oldest_audio():
    buffer = PrerollRingBuffer(capacity_ms=2 * FRAME_MS)

    buffer.write(_frame(1) + _frame(2) + _frame(3))

    assert buffer.read_preroll() == _frame(2) + _frame(3)
    assert buffer.size_bytes == 2 * FRAME_BYTES


def test_wraparound_keeps_exactly_the_newest_capacity():
    buffer = PrerollRingBuffer(capacity_ms=4 * FRAME_MS)
    written = [_frame(i) for i in range(10)]

    for chunk in written:
        buffer.write(chunk)

    assert buffer.size_bytes == 4 * FRAME_BYTES
    assert buffer.read_preroll() == b"".join(written[-4:])


def test_read_preroll_returns_a_snapshot():
    buffer = PrerollRingBuffer(capacity_ms=FRAME_MS)
    buffer.write(_frame(1))

    snapshot = buffer.read_preroll()
    buffer.write(_frame(2))

    assert snapshot == _frame(1)
    assert buffer.read_preroll() == _frame(2)


def test_clear_drops_everything():
    buffer = PrerollRingBuffer(capacity_ms=FRAME_MS)
    buffer.write(_frame(1))

    buffer.clear()

    assert buffer.read_preroll() == b""
    assert buffer.size_bytes == 0


def test_capacity_below_one_frame_is_rejected():
    with pytest.raises(ValueError):
        PrerollRingBuffer(capacity_ms=FRAME_MS - 1)
