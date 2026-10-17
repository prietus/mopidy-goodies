import logging
import pathlib

from mopidy import config, ext

__version__ = "0.13.0"

logger = logging.getLogger(__name__)


class Extension(ext.Extension):
    dist_name = "Mopidy-Goodies"
    ext_name = "goodies"
    version = __version__

    def get_default_config(self):
        return (pathlib.Path(__file__).parent / "ext.conf").read_text()

    def get_config_schema(self):
        schema = super().get_config_schema()
        # Path to the named pipe the operator wired into ``[audio] output``
        # via a ``tee ! ... ! filesink location=…`` branch. When set, the
        # ``/goodies/audio/visualizer`` WebSocket streams raw PCM chunks
        # from it to connected clients. Optional — leaving it unset just
        # disables the visualizer endpoint.
        schema["visualizer_fifo"] = config.Path(optional=True)
        # Preferred over the FIFO: the tee's visualizer branch ends in
        # ``udpsink host=127.0.0.1 port=<this>``. Sending never blocks, so a
        # missing or stuck reader can't stall playback (a FIFO can).
        schema["visualizer_udp_port"] = config.Port(optional=True)
        return schema

    def setup(self, registry):
        from .handlers import factory
        from .stats import PlaybackHistoryFrontend

        registry.add(
            "http:app",
            {"name": self.ext_name, "factory": factory},
        )
        registry.add("frontend", PlaybackHistoryFrontend)
