"""Resolve Mopidy's ``[audio] output`` to a human-readable device record.

Mopidy's ``output`` is a GStreamer bin spec — anything that ``gst-launch-1.0``
would accept as a sink. Common shapes:

    alsasink device=hw:1,0
    alsasink device=hw:CARD=D90III,DEV=0
    alsasink device=plughw:Topping
    pulsesink
    autoaudiosink
    pipewiresink target-object=Topping

For ``alsasink`` we extract the card and look it up in
``/proc/asound/cards``. For other sinks (or non-Linux hosts) we return what
we can parse and leave ``card`` as ``None`` so the client can fall back to
the raw device string.
"""
import logging
import re
import shlex
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CARDS_PATH = Path("/proc/asound/cards")
DEFAULT_PROC_ROOT = Path("/proc/asound")


def describe(audio_config, cards_path=DEFAULT_CARDS_PATH):
    """Return ``{sink, device, card}`` for the configured output, or ``None``.

    ``audio_config`` is the ``config["audio"]`` dict Mopidy passes to the
    HTTP factory.
    """
    output = (audio_config or {}).get("output")
    if not output:
        return None
    sink, params = _parse_bin(output)
    if not sink:
        return None
    device = params.get("device")
    card = None
    if sink in ("alsasink", "alsasrc") and device:
        card = _resolve_alsa_card(device, cards_path)
    return {"sink": sink, "device": device, "card": card}


def runtime(
    audio_config,
    cards_path=DEFAULT_CARDS_PATH,
    proc_root=DEFAULT_PROC_ROOT,
):
    """Combined runtime + static view of the audio chain.

    Returns ``{output, active, format, chain}``:
    - ``output`` — same shape as :func:`describe`
    - ``active`` — ``True`` if ALSA has an open substream on the configured DAC
    - ``format`` — ``{rate, bits, channels, alsa_format}`` from
      ``/proc/asound/card<N>/pcm<DEV>p/sub0/hw_params``, or ``None`` if idle
    - ``chain`` — static analysis of the pipeline (see :func:`analyze_chain`)
    """
    out = describe(audio_config, cards_path=cards_path)
    chain = analyze_chain(audio_config)
    fmt = None
    card_idx = (out or {}).get("card", {}) and out["card"].get("index")
    if card_idx is not None:
        dev = _alsa_dev_index((out or {}).get("device") or "")
        fmt = read_hw_params(card_idx, dev=dev, proc_root=proc_root)
    return {
        "output": out,
        "active": fmt is not None,
        "format": fmt,
        "chain": chain,
    }


def analyze_chain(audio_config):
    """Static read of ``[audio]`` to decide if the chain is bit-perfect-capable.

    We can't introspect GStreamer's actual pipeline from here, so this is a
    config-only heuristic. Verdicts:

    - ``"bit-perfect"`` — ``alsasink`` directly bound to ``hw:`` (no plug,
      no dmix), no ``audioresample``/``audioconvert`` element in the bin
      spec, and ``mixer = none``.
    - ``"not-bit-perfect"`` — software mixer, ``plughw:``/``dmix``/``dsnoop``,
      or an explicit resampler/converter.
    - ``"unknown"`` — non-ALSA sink (``pulsesink``, ``pipewiresink``,
      ``autoaudiosink``, …) where bit-perfect-ness depends on settings we
      can't see from here.
    """
    cfg = audio_config or {}
    output = (cfg.get("output") or "").strip()
    mixer = (cfg.get("mixer") or "").strip().lower()
    sink, params = _parse_bin(output)
    output_l = output.lower()

    direct_hw = bool(
        sink in ("alsasink",)
        and params.get("device", "").startswith(("hw:",))
    )
    no_resample = "audioresample" not in output_l
    no_convert = "audioconvert" not in output_l
    no_plug = "plughw:" not in output_l
    no_dmix = "dmix" not in output_l and "dsnoop" not in output_l
    no_mixer = mixer in ("", "none")

    chain = {
        "direct_hw": direct_hw,
        "no_mixer": no_mixer,
        "no_resample": no_resample,
        "no_convert": no_convert,
    }
    if not sink:
        chain["verdict"] = "unknown"
    elif sink != "alsasink":
        # pulsesink / pipewiresink / autoaudiosink may or may not pass-through.
        chain["verdict"] = "unknown"
    elif direct_hw and no_mixer and no_resample and no_convert and no_plug and no_dmix:
        chain["verdict"] = "bit-perfect"
    else:
        chain["verdict"] = "not-bit-perfect"
    return chain


# ── /proc/asound runtime probe ─────────────────────────────────────────────


# ALSA exposes the negotiated PCM params at:
#   /proc/asound/card<N>/pcm<DEV>p/sub<S>/hw_params
# When the device is open it looks like:
#   access: MMAP_INTERLEAVED
#   format: S32_LE
#   subformat: STD
#   channels: 2
#   rate: 44100 (44100/1)
#   ...
# When closed the file contains just "closed\n".
def read_hw_params(card_index, dev=0, sub=0, proc_root=DEFAULT_PROC_ROOT):
    """Read the live PCM params Mopidy → ALSA negotiated, or ``None``."""
    path = Path(proc_root) / f"card{card_index}" / f"pcm{dev}p" / f"sub{sub}" / "hw_params"
    try:
        text = path.read_text()
    except OSError:
        return None
    text = text.strip()
    if not text or text == "closed":
        return None
    fields = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fields[k.strip()] = v.strip()
    fmt = fields.get("format")
    channels_s = fields.get("channels", "")
    rate_s = fields.get("rate", "").split()[0] if fields.get("rate") else ""
    if not (fmt and channels_s.isdigit() and rate_s.isdigit()):
        return None
    return {
        "rate": int(rate_s),
        "bits": _alsa_format_bits(fmt),
        "channels": int(channels_s),
        "alsa_format": fmt,
    }


# S32_LE → 32, S24_3LE → 24, FLOAT_LE → 32, DSD_U32_BE → 32.
# We report the *container* width; the source bit depth isn't recoverable here.
_FORMAT_BITS = re.compile(r"(\d+)")


def _alsa_format_bits(fmt):
    if not fmt:
        return None
    if fmt.startswith("FLOAT"):
        return 32 if "64" not in fmt else 64
    m = _FORMAT_BITS.search(fmt)
    return int(m.group(1)) if m else None


def _parse_bin(spec):
    """``alsasink device=hw:1,0 sync=false`` → (``alsasink``, ``{...}``).

    Only the leading element of the pipeline is inspected (i.e. text before
    the first ``!``). ``shlex`` handles quoted values like ``device="hw:1,0"``.
    """
    head = spec.strip().split("!", 1)[0].strip()
    if not head:
        return None, {}
    try:
        tokens = shlex.split(head, posix=True)
    except ValueError:
        return None, {}
    if not tokens:
        return None, {}
    sink = tokens[0]
    params = {}
    for tok in tokens[1:]:
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        params[k.strip()] = v.strip()
    return sink, params


# /proc/asound/cards first-line format:
#  1 [D90III         ]: USB-Audio - Topping D90 III SABRE
_CARDS_LINE = re.compile(
    r"^\s*(?P<index>\d+)\s*\[(?P<id>[^\]]+)\]\s*:\s*"
    r"(?P<kind>\S+)\s*-\s*(?P<longname>.+?)\s*$"
)


def _resolve_alsa_card(device, cards_path):
    """Map an ALSA device string to a ``{index, id, name}`` record.

    Accepts ``hw:N``, ``hw:N,M``, ``hw:CardID``, ``hw:CARD=CardID,DEV=N``,
    and the ``plughw:``/``default:`` variants. Returns ``None`` if the card
    can't be identified (e.g. ``default``, non-Linux, unparseable).
    """
    target = _alsa_target(device)
    if target is None:
        return None
    cards = _read_cards(cards_path)
    if not cards:
        return None
    if target.isdigit():
        return cards.get(int(target))
    for c in cards.values():
        if c["id"] == target:
            return c
    return None


def _alsa_target(device):
    """Strip ``hw:``/``plughw:``/``default:`` and pull the card portion.

    Returns ``None`` for plain ``default`` (no card binding) or empty.
    """
    rest = re.sub(r"^(plughw|hw|default):", "", device.strip(), count=1)
    if not rest or rest == "default":
        return None
    # CARD=X,DEV=Y form
    m = re.search(r"CARD=([^,]+)", rest)
    if m:
        return m.group(1).strip()
    # N,M or just N/CardID form
    return rest.split(",", 1)[0].strip() or None


def _alsa_dev_index(device):
    """Return the PCM device number (``DEV=N`` or second positional), default 0.

    Accepts the same forms as :func:`_alsa_target`. Falls back to ``0`` for
    unrecognised input — primary playback PCM is the common case.
    """
    if not device:
        return 0
    rest = re.sub(r"^(plughw|hw|default):", "", device.strip(), count=1)
    m = re.search(r"DEV=(\d+)", rest)
    if m:
        return int(m.group(1))
    parts = rest.split(",", 1)
    if len(parts) == 2 and parts[1].strip().isdigit():
        return int(parts[1].strip())
    return 0


def _read_cards(path):
    try:
        text = Path(path).read_text()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        m = _CARDS_LINE.match(line)
        if not m:
            continue
        idx = int(m["index"])
        out[idx] = {
            "index": idx,
            "id": m["id"].strip(),
            "name": m["longname"].strip(),
        }
    return out
