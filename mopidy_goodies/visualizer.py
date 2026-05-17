"""Audio visualizer feed: stream raw PCM from a GStreamer FIFO over WebSocket.

Setup (operator side):

  1. In ``mopidy.conf`` ``[audio] output``, branch the pipeline with ``tee``
     so one rama drives ``alsasink`` (bit-perfect) and the other writes
     PCM to a FIFO::

       output = tee name=t
         t. ! queue ! alsasink device=hw:CARD=SABRE,DEV=0 buffer-time=200000
         t. ! queue leaky=downstream max-size-buffers=200
            ! audioconvert ! audioresample
            ! audio/x-raw,format=S16LE,rate=44100,channels=2
            ! filesink location=/tmp/mopidy.fifo sync=false

  2. ``mkfifo /tmp/mopidy.fifo`` (once).
  3. ``[goodies] visualizer_fifo = /tmp/mopidy.fifo``.

Clients connecting to ``ws://host:6680/goodies/audio/visualizer`` get raw
binary frames as they arrive — interpret as ``S16LE`` at the rate/channels
the operator configured on the FIFO branch (convention: 44.1 kHz stereo
unless the operator changed it).

Design notes:

* The FIFO is single-reader by kernel contract. One ``FifoReader`` thread
  per goodies process opens it; that thread fans chunks out to every
  connected WebSocket.
* The reader is lazy: it spins up on first WS connect, shuts down when
  the last client disconnects, so an idle server isn't pinned on a
  blocking read.
* Reads are blocking in a thread; broadcasts hop back to the Tornado
  IOLoop via ``add_callback``. Never touch a ``WebSocketHandler`` from
  the reader thread directly.
"""
import logging
import os
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
            try:
                # Blocking open: returns once a writer (GStreamer filesink)
                # has the other end. If mopidy isn't playing, this just waits.
                with open(self.path, "rb") as f:
                    logger.info("visualizer FIFO opened: %s", self.path)
                    while not self._stop.is_set():
                        chunk = f.read(CHUNK_BYTES)
                        if not chunk:
                            # Writer closed (e.g. mopidy paused → GStreamer
                            # may tear down the branch). Loop will reopen.
                            break
                        self.loop.add_callback(self.on_chunk, chunk)
            except FileNotFoundError:
                logger.warning("visualizer FIFO missing: %s", self.path)
                time.sleep(1)
            except Exception:
                logger.exception("visualizer FIFO read error")
                time.sleep(1)
        logger.info("visualizer FIFO reader stopped")


class VisualizerWebSocket(WebSocketHandler):
    """Broadcasts PCM chunks to all connected clients.

    Class-level state (``_clients`` / ``_reader``) is fine because there is
    a single Tornado IOLoop in the Mopidy http extension and all access
    happens on that loop's thread.
    """

    _clients: set["VisualizerWebSocket"] = set()
    _reader: FifoReader | None = None

    def initialize(self, core, config):
        self.core = core
        self.config = config

    def check_origin(self, origin):
        # Same-origin restriction is wrong here — clients are mopytui /
        # mopyrust running on the LAN, not browsers. The Mopidy http server
        # already binds locally; access control is the operator's concern.
        return True

    def open(self):
        fifo = self._fifo_path()
        if not fifo:
            self.close(code=1011, reason="visualizer not configured")
            return
        VisualizerWebSocket._clients.add(self)
        logger.debug("visualizer WS open (%d total)", len(self._clients))
        if VisualizerWebSocket._reader is None:
            VisualizerWebSocket._reader = FifoReader(
                fifo, IOLoop.current(), VisualizerWebSocket._broadcast
            )
            VisualizerWebSocket._reader.start()

    def on_close(self):
        VisualizerWebSocket._clients.discard(self)
        logger.debug("visualizer WS close (%d remaining)", len(self._clients))
        if not VisualizerWebSocket._clients and VisualizerWebSocket._reader:
            VisualizerWebSocket._reader.stop()
            VisualizerWebSocket._reader = None

    def on_message(self, message):
        # Visualizer feed is server→client only. Ignore anything the client
        # sends rather than erroring — keeps the protocol forgiving for
        # heartbeats clients might send.
        pass

    def _fifo_path(self) -> str | None:
        section = (self.config or {}).get("goodies") or {}
        path = section.get("visualizer_fifo")
        if path and os.path.exists(path):
            return path
        return None

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


def visualizer_active(config) -> bool:
    """True iff the FIFO path is configured and the file actually exists."""
    section = (config or {}).get("goodies") or {}
    path = section.get("visualizer_fifo")
    return bool(path) and os.path.exists(path)
