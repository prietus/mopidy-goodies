"""Audio visualizer feed: stream raw PCM from a GStreamer side branch over WebSocket.

Setup (operator side): branch ``[audio] output`` with a ``tee`` so one branch
drives ``alsasink`` (untouched, bit-perfect) and the other converts to S16LE
44.1 kHz stereo (what clients assume) and sends it to goodies, which fans it out to every client on
``ws://host:6680/goodies/audio/visualizer``.

UDP (recommended)::

  output = tee name=t  t. ! queue ! alsasink device=hw:CARD=SABRE,DEV=0  t. ! queue leaky=downstream max-size-buffers=200 ! audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=44100,channels=2 ! udpsink host=127.0.0.1 port=5555 sync=false async=false

  [goodies]
  visualizer_udp_port = 5555

A FIFO (``filesink location=…`` + ``visualizer_fifo``) also works but is
fragile, and is kept only for existing setups. Measured on a real server:

* With nobody reading the FIFO (goodies off, or not configured) ``filesink``
  blocks opening it and Mopidy hangs on play.
* With a reader that stops reading, the FIFO fills, ``filesink`` blocks in
  ``write()`` and end-of-stream can't get past it: the track never ends and
  the queue never advances. The DAC branch still gets every sample.

UDP has neither problem: sending never blocks and needs no receiver (the
datagrams are just dropped), and the DAC branch is byte-identical either way.

Design notes:

* One process-wide reader (``ensure_reader()``, called at http-app factory
  time) receives the PCM and fans it out to every connected WebSocket. With
  the FIFO it must exist before playback starts, or ``filesink`` errors.
* Reads block in a thread; broadcasts hop to the Tornado IOLoop via
  ``add_callback``. Never touch a ``WebSocketHandler`` from the reader thread.
"""
import logging
import os
import select
import socket
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
        # Not "_stop": up to Python 3.12 that name is a Thread method join() calls.
        self._stopping = threading.Event()

    def stop(self):
        self._stopping.set()

    def run(self):
        while not self._stopping.is_set():
            fd = self._open_nonblocking()
            if fd is None:
                # Path missing or other open error — wait briefly and retry.
                if self._stopping.wait(1.0):
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
        which is interruptible by ``_stopping``.
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
        ``_stopping``. EOF (read returns 0 after writer closes) makes us
        return so the outer loop can re-open."""
        # 0.5 s select timeout keeps the loop responsive to _stopping without
        # burning CPU when nothing's coming.
        while not self._stopping.is_set():
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
                # just loop and check _stopping again.
                continue
            except OSError:
                logger.exception("visualizer FIFO read error")
                return
            if not chunk:
                # EOF — writer closed. Outer loop will reopen the FIFO.
                return
            self.loop.add_callback(self.on_chunk, chunk)


class UdpReader(threading.Thread):
    """Receives the PCM datagrams ``udpsink`` sends to ``127.0.0.1:<port>``
    and emits each via ``on_chunk(bytes)`` on the Tornado IOLoop thread."""

    MAX_DATAGRAM = 65536

    def __init__(self, port: int, loop: IOLoop, on_chunk, host: str = "127.0.0.1"):
        super().__init__(daemon=True, name="goodies-visualizer-udp")
        self.port = port
        self.host = host
        self.loop = loop
        self.on_chunk = on_chunk
        self._stopping = threading.Event()
        self._sock = None
        self.ready = threading.Event()  # bound and receiving (for tests)

    def stop(self):
        self._stopping.set()

    def run(self):
        while not self._stopping.is_set():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
            except OSError:
                logger.exception("visualizer: can't listen on udp %s:%d", self.host, self.port)
                if self._stopping.wait(5.0):
                    break
                continue
            # A timeout keeps the loop responsive to stop() without busy-waiting.
            sock.settimeout(0.5)
            logger.info("visualizer: listening on udp %s:%d", self.host, self.port)
            self.ready.set()
            try:
                while not self._stopping.is_set():
                    try:
                        chunk = sock.recv(self.MAX_DATAGRAM)
                    except socket.timeout:
                        continue
                    except OSError:
                        logger.exception("visualizer: udp receive error")
                        break
                    if chunk:
                        self.loop.add_callback(self.on_chunk, chunk)
            finally:
                sock.close()
        logger.info("visualizer udp reader stopped")


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
        if not visualizer_active(self.config):
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


# Process-wide reader: started at http factory time so a FIFO has a reader
# from before GStreamer ever tries to write to it.
_global_reader: threading.Thread | None = None
_global_reader_lock = threading.Lock()


def ensure_reader(config, loop: IOLoop) -> None:
    """Idempotently start the reader for the configured transport: UDP
    (``visualizer_udp_port``) if set, else the FIFO (``visualizer_fifo``).

    Called from the http app factory once per process. Safe to call
    multiple times — second and later calls are no-ops.
    """
    global _global_reader
    port, fifo = _udp_port(config), _fifo_path(config)
    if not port and not fifo:
        return
    with _global_reader_lock:
        if _global_reader is not None and _global_reader.is_alive():
            return
        if port:
            _global_reader = UdpReader(port, loop, VisualizerWebSocket._broadcast)
        else:
            _global_reader = FifoReader(fifo, loop, VisualizerWebSocket._broadcast)
        _global_reader.start()
        logger.info("visualizer: reader started (%s)", f"udp {port}" if port else fifo)


def _section(config):
    return (config or {}).get("goodies") or {}


def _udp_port(config) -> int | None:
    return _section(config).get("visualizer_udp_port") or None


def _fifo_path(config) -> str | None:
    """Return the configured FIFO path if it exists on disk, else None."""
    path = _section(config).get("visualizer_fifo")
    if path and os.path.exists(path):
        return path
    return None


def visualizer_transport(config) -> str | None:
    """``"udp"``, ``"fifo"`` or None when the visualizer isn't set up."""
    if _udp_port(config):
        return "udp"
    if _fifo_path(config):
        return "fifo"
    return None


def visualizer_active(config) -> bool:
    """True when a UDP port is configured, or the FIFO path is configured and exists."""
    return visualizer_transport(config) is not None
