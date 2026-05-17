"""Audio visualizer feed: stream raw PCM from a GStreamer FIFO over WebSocket.

Setup (operator side):

  1. In ``mopidy.conf`` ``[audio] output``, branch the pipeline with ``tee``
     so one rama drives ``alsasink`` (bit-perfect) and the other writes
     PCM to a FIFO. Single line — INI's continuation rules are too brittle
     for a GStreamer bin spec::

       output = tee name=t  t. ! queue ! alsasink device=hw:CARD=SABRE,DEV=0 buffer-time=200000  t. ! queue leaky=downstream max-size-buffers=200 ! audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=48000,channels=2 ! filesink location=/tmp/mopidy.fifo sync=false

  2. ``mkfifo /tmp/mopidy.fifo`` (once).
  3. ``[goodies] visualizer_fifo = /tmp/mopidy.fifo``.

Clients connecting to ``ws://host:6680/goodies/audio/visualizer`` get raw
binary frames as they arrive — interpret as ``S16LE`` at the rate/channels
the operator configured on the FIFO branch (convention: 48 kHz stereo
unless the operator changed it).

Design notes:

* GStreamer's ``filesink`` errors out if it tries to write to the FIFO
  with no reader on the other end — and that error propagates through
  the bin and kills the ``alsasink`` rama too. So the reader has to be
  open *before* playback starts, not just when a WS client connects.
  ``ensure_reader()`` starts a single process-wide ``FifoReader`` at
  http-app factory time (once per Mopidy startup) and keeps it running
  for the whole process lifetime. If no WS clients are connected the
  thread still reads and discards — ~200 KB/s of memcpy is cheap.
* The FIFO is single-reader by kernel contract. One ``FifoReader`` thread
  per goodies process opens it; that thread fans chunks out to every
  connected WebSocket.
* Reads are blocking in a thread; broadcasts hop back to the Tornado
  IOLoop via ``add_callback``. Never touch a ``WebSocketHandler`` from
  the reader thread directly.
"""
import logging
import os
import select
import threading
import time

from tornado.ioloop import IOLoop
from tornado.websocket import WebSocketHandler

logger = logging.getLogger(__name__)

# 4096 bytes = 1024 stereo S16 frames ≈ 23 ms at 44.1 kHz. Small enough
# to feel real-time, large enough that we're not doing 1000 broadcasts/sec.
CHUNK_BYTES = 4096


class FifoReader(threading.Thread):
    """Blocking reader for a named pipe; emits chunks via ``on_chunk(bytes)``
    on the Tornado IOLoop thread."""

    def __init__(self, path: str, loop: IOLoop, on_chunk):
        super().__init__(daemon=True, name="goodies-visualizer-fifo")
        self.path = path
        self.loop = loop
        self.on_chunk = on_chunk
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            fd = self._open_nonblocking()
            if fd is None:
                # Path missing or other open error — wait briefly and retry.
                if self._stop.wait(1.0):
                    break
                continue
            try:
                logger.info("visualizer FIFO opened: %s", self.path)
                self._read_loop(fd)
            finally:
                os.close(fd)
        logger.info("visualizer FIFO reader stopped")

    def _open_nonblocking(self) -> int | None:
        """Open the FIFO read-only in non-blocking mode.

        A blocking ``open()`` on a FIFO with no writer parks the calling
        thread inside the kernel until a writer shows up, *and that
        syscall ignores Python's interpreter shutdown* — daemon threads
        stuck there keep the process alive, so systemd ends up
        SIGKILL'ing mopidy on stop. ``O_NONBLOCK`` returns immediately
        regardless of writer state; we then ``select`` for readability,
        which is interruptible by ``_stop``.
        """
        try:
            return os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            logger.warning("visualizer FIFO missing: %s", self.path)
            return None
        except OSError:
            logger.exception("visualizer FIFO open error")
            return None

    def _read_loop(self, fd: int) -> None:
        """Pump bytes from ``fd`` to ``on_chunk`` until EOF, error, or
        ``_stop``. EOF (read returns 0 after writer closes) makes us
        return so the outer loop can re-open."""
        # 0.5 s select timeout keeps the loop responsive to _stop without
        # burning CPU when nothing's coming.
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.5)
            except (OSError, InterruptedError):
                return
            if not ready:
                continue
            try:
                chunk = os.read(fd, CHUNK_BYTES)
            except BlockingIOError:
                # select said ready but nothing actually there — rare race;
                # just loop and check _stop again.
                continue
            except OSError:
                logger.exception("visualizer FIFO read error")
                return
            if not chunk:
                # EOF — writer closed. Outer loop will reopen the FIFO.
                return
            self.loop.add_callback(self.on_chunk, chunk)


class VisualizerWebSocket(WebSocketHandler):
    """Registers/unregisters as a broadcast target. The actual FIFO read
    lives in the process-wide reader started by :func:`ensure_reader`."""

    _clients: set["VisualizerWebSocket"] = set()

    def initialize(self, core, config):
        self.core = core
        self.config = config

    def check_origin(self, origin):
        # Same-origin restriction is wrong here — clients are mopytui /
        # mopyrust running on the LAN, not browsers. The Mopidy http server
        # already binds locally; access control is the operator's concern.
        return True

    def open(self):
        if not _fifo_path(self.config):
            self.close(code=1011, reason="visualizer not configured")
            return
        VisualizerWebSocket._clients.add(self)
        logger.debug("visualizer WS open (%d total)", len(self._clients))

    def on_close(self):
        VisualizerWebSocket._clients.discard(self)
        logger.debug("visualizer WS close (%d remaining)", len(self._clients))

    def on_message(self, message):
        # Visualizer feed is server→client only. Ignore anything the client
        # sends rather than erroring — keeps the protocol forgiving for
        # heartbeats clients might send.
        pass

    @classmethod
    def _broadcast(cls, chunk: bytes):
        if not cls._clients:
            return
        dead = []
        for client in cls._clients:
            try:
                client.write_message(chunk, binary=True)
            except Exception:
                # Client went away mid-write; collect and prune after the
                # loop so we don't mutate the set while iterating.
                dead.append(client)
        for client in dead:
            cls._clients.discard(client)


# Process-wide reader: started at http factory time so the FIFO has a
# reader from before GStreamer ever tries to write to it. See module
# docstring for why "lazy on first WS connect" doesn't work.
_global_reader: FifoReader | None = None
_global_reader_lock = threading.Lock()


def ensure_reader(config, loop: IOLoop) -> None:
    """Idempotently start the FIFO reader if ``visualizer_fifo`` is set.

    Called from the http app factory once per process. Safe to call
    multiple times — second and later calls are no-ops.
    """
    global _global_reader
    fifo = _fifo_path(config)
    if not fifo:
        return
    with _global_reader_lock:
        if _global_reader is not None and _global_reader.is_alive():
            return
        _global_reader = FifoReader(fifo, loop, VisualizerWebSocket._broadcast)
        _global_reader.start()
        logger.info("visualizer: process-wide FIFO reader started for %s", fifo)


def _fifo_path(config) -> str | None:
    """Return the configured FIFO path if it exists on disk, else None."""
    section = (config or {}).get("goodies") or {}
    path = section.get("visualizer_fifo")
    if path and os.path.exists(path):
        return path
    return None


def visualizer_active(config) -> bool:
    """True iff the FIFO path is configured and the file actually exists."""
    return _fifo_path(config) is not None
