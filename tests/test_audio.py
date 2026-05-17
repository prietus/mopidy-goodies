"""Unit tests for :mod:`mopidy_goodies.audio` — pure-function logic,
no Mopidy required."""
from pathlib import Path

import pytest

from mopidy_goodies import audio


# Realistic /proc/asound/cards content from a Mopidy box with onboard HDA +
# a Topping D90 III SABRE plugged in over USB.
CARDS_FIXTURE = """\
 0 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0xf0700000 irq 130
 1 [D90III         ]: USB-Audio - Topping D90 III SABRE
                      Topping D90 III SABRE at usb-0000:00:14.0-3, high speed
"""


@pytest.fixture
def cards(tmp_path):
    p = tmp_path / "cards"
    p.write_text(CARDS_FIXTURE)
    return p


# ── _parse_bin ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec,expected_sink,expected_params",
    [
        ("alsasink device=hw:1,0", "alsasink", {"device": "hw:1,0"}),
        ("alsasink", "alsasink", {}),
        ("  alsasink   device=hw:Topping  ", "alsasink", {"device": "hw:Topping"}),
        ("pulsesink", "pulsesink", {}),
        ("pipewiresink target-object=Topping", "pipewiresink", {"target-object": "Topping"}),
        ('alsasink device="hw:1,0"', "alsasink", {"device": "hw:1,0"}),
        ("alsasink device=hw:1,0 sync=false", "alsasink", {"device": "hw:1,0", "sync": "false"}),
        ("alsasink device=hw:1,0 ! fakesink", "alsasink", {"device": "hw:1,0"}),
        ("", None, {}),
        ("   ", None, {}),
    ],
)
def test_parse_bin(spec, expected_sink, expected_params):
    sink, params = audio._parse_bin(spec)
    assert sink == expected_sink
    assert params == expected_params


# ── _alsa_target ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "device,expected",
    [
        ("hw:1,0", "1"),
        ("hw:1", "1"),
        ("hw:Topping", "Topping"),
        ("plughw:1,0", "1"),
        ("plughw:Topping", "Topping"),
        ("hw:CARD=D90III,DEV=0", "D90III"),
        ("default:CARD=D90III", "D90III"),
        ("default", None),
        ("", None),
    ],
)
def test_alsa_target(device, expected):
    assert audio._alsa_target(device) == expected


# ── _read_cards ────────────────────────────────────────────────────────────


def test_read_cards_parses_proc(cards):
    parsed = audio._read_cards(cards)
    assert parsed == {
        0: {"index": 0, "id": "PCH", "name": "HDA Intel PCH"},
        1: {"index": 1, "id": "D90III", "name": "Topping D90 III SABRE"},
    }


def test_read_cards_missing_file_is_empty(tmp_path):
    assert audio._read_cards(tmp_path / "nope") == {}


# ── _resolve_alsa_card ─────────────────────────────────────────────────────


def test_resolve_by_index(cards):
    assert audio._resolve_alsa_card("hw:1,0", cards) == {
        "index": 1, "id": "D90III", "name": "Topping D90 III SABRE",
    }


def test_resolve_by_id(cards):
    assert audio._resolve_alsa_card("hw:D90III", cards) == {
        "index": 1, "id": "D90III", "name": "Topping D90 III SABRE",
    }


def test_resolve_plughw(cards):
    assert audio._resolve_alsa_card("plughw:0,0", cards) == {
        "index": 0, "id": "PCH", "name": "HDA Intel PCH",
    }


def test_resolve_card_form(cards):
    assert audio._resolve_alsa_card("hw:CARD=D90III,DEV=0", cards) == {
        "index": 1, "id": "D90III", "name": "Topping D90 III SABRE",
    }


def test_resolve_unknown_returns_none(cards):
    assert audio._resolve_alsa_card("hw:99", cards) is None
    assert audio._resolve_alsa_card("hw:DoesNotExist", cards) is None


def test_resolve_default_returns_none(cards):
    assert audio._resolve_alsa_card("default", cards) is None


def test_resolve_missing_proc_returns_none(tmp_path):
    assert audio._resolve_alsa_card("hw:1,0", tmp_path / "missing") is None


# ── describe (end-to-end) ──────────────────────────────────────────────────


def test_describe_alsasink_resolves_card(cards):
    info = audio.describe({"output": "alsasink device=hw:1,0"}, cards_path=cards)
    assert info == {
        "sink": "alsasink",
        "device": "hw:1,0",
        "card": {"index": 1, "id": "D90III", "name": "Topping D90 III SABRE"},
    }


def test_describe_alsasink_by_id(cards):
    info = audio.describe({"output": "alsasink device=hw:D90III"}, cards_path=cards)
    assert info["card"]["name"] == "Topping D90 III SABRE"


def test_describe_pulsesink_has_no_card(cards):
    info = audio.describe({"output": "pulsesink"}, cards_path=cards)
    assert info == {"sink": "pulsesink", "device": None, "card": None}


def test_describe_pipewire_passthrough(cards):
    info = audio.describe(
        {"output": "pipewiresink target-object=Topping"}, cards_path=cards
    )
    assert info["sink"] == "pipewiresink"
    assert info["device"] is None
    assert info["card"] is None


def test_describe_alsasink_default_device(cards):
    info = audio.describe({"output": "alsasink device=default"}, cards_path=cards)
    assert info == {"sink": "alsasink", "device": "default", "card": None}


def test_describe_no_config_returns_none(cards):
    assert audio.describe(None, cards_path=cards) is None
    assert audio.describe({}, cards_path=cards) is None
    assert audio.describe({"output": ""}, cards_path=cards) is None


def test_describe_unknown_card_keeps_device(cards):
    info = audio.describe({"output": "alsasink device=hw:42,0"}, cards_path=cards)
    assert info == {"sink": "alsasink", "device": "hw:42,0", "card": None}


# ── _alsa_dev_index ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "device,expected",
    [
        ("hw:1,0", 0),
        ("hw:1,3", 3),
        ("hw:1", 0),
        ("hw:Topping", 0),
        ("hw:CARD=SABRE,DEV=0", 0),
        ("hw:CARD=SABRE,DEV=2", 2),
        ("plughw:0,1", 1),
        ("default", 0),
        ("", 0),
        (None, 0),
    ],
)
def test_alsa_dev_index(device, expected):
    assert audio._alsa_dev_index(device) == expected


# ── read_hw_params ─────────────────────────────────────────────────────────


HW_PARAMS_PLAYING = """\
access: MMAP_INTERLEAVED
format: S32_LE
subformat: STD
channels: 2
rate: 44100 (44100/1)
period_size: 11025
buffer_size: 22050
"""


def _write_hw_params(proc_root, card, dev, sub, content):
    p = proc_root / f"card{card}" / f"pcm{dev}p" / f"sub{sub}" / "hw_params"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_read_hw_params_when_playing(tmp_path):
    _write_hw_params(tmp_path, 0, 0, 0, HW_PARAMS_PLAYING)
    assert audio.read_hw_params(0, dev=0, sub=0, proc_root=tmp_path) == {
        "rate": 44100,
        "bits": 32,
        "channels": 2,
        "alsa_format": "S32_LE",
    }


def test_read_hw_params_closed_returns_none(tmp_path):
    _write_hw_params(tmp_path, 0, 0, 0, "closed\n")
    assert audio.read_hw_params(0, proc_root=tmp_path) is None


def test_read_hw_params_missing_returns_none(tmp_path):
    assert audio.read_hw_params(0, proc_root=tmp_path) is None


def test_read_hw_params_hires(tmp_path):
    _write_hw_params(tmp_path, 1, 0, 0, """\
access: MMAP_INTERLEAVED
format: S24_3LE
subformat: STD
channels: 2
rate: 192000 (192000/1)
""")
    info = audio.read_hw_params(1, proc_root=tmp_path)
    assert info == {
        "rate": 192000,
        "bits": 24,
        "channels": 2,
        "alsa_format": "S24_3LE",
    }


def test_read_hw_params_dsd(tmp_path):
    _write_hw_params(tmp_path, 0, 0, 0, """\
access: MMAP_INTERLEAVED
format: DSD_U32_BE
subformat: STD
channels: 2
rate: 352800 (352800/1)
""")
    info = audio.read_hw_params(0, proc_root=tmp_path)
    assert info["alsa_format"] == "DSD_U32_BE"
    assert info["bits"] == 32
    assert info["rate"] == 352800


# ── analyze_chain ──────────────────────────────────────────────────────────


def test_chain_bit_perfect():
    chain = audio.analyze_chain(
        {"output": "alsasink device=hw:CARD=SABRE,DEV=0", "mixer": "none"}
    )
    assert chain == {
        "direct_hw": True,
        "no_mixer": True,
        "no_resample": True,
        "no_convert": True,
        "verdict": "bit-perfect",
    }


def test_chain_software_mixer_breaks_bit_perfect():
    chain = audio.analyze_chain(
        {"output": "alsasink device=hw:1,0", "mixer": "software"}
    )
    assert chain["no_mixer"] is False
    assert chain["verdict"] == "not-bit-perfect"


def test_chain_plughw_breaks_bit_perfect():
    chain = audio.analyze_chain(
        {"output": "alsasink device=plughw:1,0", "mixer": "none"}
    )
    assert chain["direct_hw"] is False
    assert chain["verdict"] == "not-bit-perfect"


def test_chain_explicit_resampler_breaks_bit_perfect():
    chain = audio.analyze_chain(
        {"output": "audioresample ! alsasink device=hw:1,0", "mixer": "none"}
    )
    # _parse_bin only sees the first element ("audioresample"), so sink is not
    # alsasink — verdict falls through to unknown. The chain flags still tell
    # the truth: no_resample is False.
    assert chain["no_resample"] is False


def test_chain_pulse_is_unknown():
    chain = audio.analyze_chain({"output": "pulsesink", "mixer": "software"})
    assert chain["verdict"] == "unknown"


def test_chain_pipewire_is_unknown():
    chain = audio.analyze_chain({"output": "pipewiresink", "mixer": "none"})
    assert chain["verdict"] == "unknown"


def test_chain_empty_config_is_unknown():
    chain = audio.analyze_chain({})
    assert chain["verdict"] == "unknown"


def test_chain_none_config_is_unknown():
    chain = audio.analyze_chain(None)
    assert chain["verdict"] == "unknown"


# ── runtime (end-to-end) ───────────────────────────────────────────────────


def test_runtime_active_bit_perfect(cards, tmp_path):
    _write_hw_params(tmp_path, 1, 0, 0, HW_PARAMS_PLAYING)
    info = audio.runtime(
        {"output": "alsasink device=hw:CARD=D90III,DEV=0", "mixer": "none"},
        cards_path=cards,
        proc_root=tmp_path,
    )
    assert info["output"]["card"]["name"] == "Topping D90 III SABRE"
    assert info["active"] is True
    assert info["format"]["rate"] == 44100
    assert info["chain"]["verdict"] == "bit-perfect"


def test_runtime_idle_keeps_chain(cards, tmp_path):
    # No hw_params file → device idle.
    info = audio.runtime(
        {"output": "alsasink device=hw:1,0", "mixer": "none"},
        cards_path=cards,
        proc_root=tmp_path,
    )
    assert info["active"] is False
    assert info["format"] is None
    assert info["chain"]["verdict"] == "bit-perfect"


def test_runtime_non_alsa(cards, tmp_path):
    info = audio.runtime(
        {"output": "pulsesink", "mixer": "software"},
        cards_path=cards,
        proc_root=tmp_path,
    )
    assert info["output"]["sink"] == "pulsesink"
    assert info["active"] is False
    assert info["format"] is None
    assert info["chain"]["verdict"] == "unknown"


def test_runtime_picks_correct_dev(cards, tmp_path):
    # DAC exposes two PCMs; we should hit pcm2p when DEV=2.
    _write_hw_params(tmp_path, 1, 0, 0, "closed\n")
    _write_hw_params(tmp_path, 1, 2, 0, HW_PARAMS_PLAYING)
    info = audio.runtime(
        {"output": "alsasink device=hw:CARD=D90III,DEV=2", "mixer": "none"},
        cards_path=cards,
        proc_root=tmp_path,
    )
    assert info["active"] is True
    assert info["format"]["rate"] == 44100
