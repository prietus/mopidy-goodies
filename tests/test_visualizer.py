"""Tests for :mod:`mopidy_goodies.visualizer`. We exercise the FIFO reader
against a real named pipe (the OS facility is exactly what we want to
verify against — mocking would just re-test our own assumptions).

The WebSocket handler itself is harder to test in isolation without
spinning up a Tornado app; we leave that for the integration smoke run
once goodies is on a server.
"""
import os
import threading
import time

import pytest

from mopidy_goodies import visualizer


@pytest.fixture
def fifo_path(tmp_path):
    p = tmp_path / "vis.fifo"
    os.mkfifo(p)
    yield str(p)


class _FakeLoop:
    """Stand-in for tornado.IOLoop that just collects callbacks. The reader
    treats it as a sink for ``add_callback(fn, chunk)``."""

    def __init__(self):
        self.chunks: list[bytes] = []
        self._lock = threading.Lock()

    def add_callback(self, fn, chunk):
        with self._lock:
            self.chunks.append(chunk)


def _drain(loop, expected_bytes, timeout=2.0):
    """Wait until we've seen ``expected_bytes`` total across collected chunks
    or time out. Returns the concatenated bytes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        total = sum(len(c) for c in loop.chunks)
        if total >= expected_bytes:
            break
        time.sleep(0.02)
    return b"".join(loop.chunks)


def test_reader_emits_writer_chunks(fifo_path):
    loop = _FakeLoop()
    reader = visualizer.FifoReader(fifo_path, loop, lambda c: loop.add_callback(None, c))
    reader.start()

    payload = b"hello-fifo" * 100  # 1000 bytes
    # Writer side: opening for write blocks until reader is open; since
    # the reader thread is already running and will hit its blocking open,
    # this open returns once both ends are wired.
    with open(fifo_path, "wb") as w:
        w.write(payload)
        w.flush()

    got = _drain(loop, len(payload))
    reader.stop()
    reader.join(timeout=2.0)
    assert got[: len(payload)] == payload


def test_reader_survives_writer_close_reopen(fifo_path):
    """When the writer closes (e.g. mopidy pauses and GStreamer tears
    down the branch), the reader should loop and accept a fresh writer."""
    loop = _FakeLoop()
    reader = visualizer.FifoReader(fifo_path, loop, lambda c: loop.add_callback(None, c))
    reader.start()

    with open(fifo_path, "wb") as w:
        w.write(b"first-")
    # Writer closed → reader hits EOF, reopens. Give it a moment.
    time.sleep(0.05)
    with open(fifo_path, "wb") as w:
        w.write(b"second")

    got = _drain(loop, 12)
    reader.stop()
    reader.join(timeout=2.0)
    assert b"first-" in got
    assert b"second" in got


def test_reader_stop_is_idempotent(fifo_path):
    loop = _FakeLoop()
    reader = visualizer.FifoReader(fifo_path, loop, lambda c: None)
    reader.start()
    reader.stop()
    reader.stop()  # must not raise
    reader.join(timeout=2.0)


def test_visualizer_active_unset():
    assert visualizer.visualizer_active(None) is False
    assert visualizer.visualizer_active({}) is False
    assert visualizer.visualizer_active({"goodies": {}}) is False


def test_visualizer_active_path_missing(tmp_path):
    cfg = {"goodies": {"visualizer_fifo": str(tmp_path / "nope")}}
    assert visualizer.visualizer_active(cfg) is False


def test_visualizer_active_path_present(fifo_path):
    cfg = {"goodies": {"visualizer_fifo": fifo_path}}
    assert visualizer.visualizer_active(cfg) is True
