import logging
import pathlib

from mopidy import config, ext

__version__ = "0.5.1"

logger = logging.getLogger(__name__)


class Extension(ext.Extension):
    dist_name = "Mopidy-Tidal-Goodies"
    ext_name = "tidal_goodies"
    version = __version__

    def get_default_config(self):
        return (pathlib.Path(__file__).parent / "ext.conf").read_text()

    def get_config_schema(self):
        schema = super().get_config_schema()
        return schema

    def setup(self, registry):
        from .handlers import factory
        from .stats import PlaybackHistoryFrontend

        registry.add(
            "http:app",
            {"name": self.ext_name, "factory": factory},
        )
        registry.add("frontend", PlaybackHistoryFrontend)
