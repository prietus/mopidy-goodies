"""Smoke tests — Extension class loads cleanly. Real handler tests need a
running Mopidy with mopidy-tidal authenticated, so they live as an integration
suite to be added later."""
from mopidy_goodies import Extension


def test_extension_metadata():
    ext = Extension()
    assert ext.dist_name == "Mopidy-Goodies"
    assert ext.ext_name == "goodies"
    assert ext.version


def test_default_config_loads():
    ext = Extension()
    cfg = ext.get_default_config()
    assert "[goodies]" in cfg
    assert "enabled = true" in cfg
