"""Radio-independent configuration model and its YAML form (docs/FORMATS.md, section 2).

Drivers decode a memory image into a :class:`Codeplug`; this module turns it
into a readable YAML document and back (:func:`load`). Frequencies are kept
in Hz internally and written in MHz. :func:`diff` compares two codeplugs for
humans (what ``write`` is about to change).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

FORMAT_VERSION = 1

# Beyond this TX/RX difference a channel is written with an explicit `tx`
# instead of an `offset` (same threshold as CHIRP's "split").
SPLIT_THRESHOLD_HZ = 70_000_000

Tone = float | str  # 88.5 | "D023N" | "off"


class CodeplugError(ValueError):
    """The configuration is invalid or cannot be written to the radio."""


def text(value: str | None) -> DoubleQuotedScalarString:
    """Free text typed by the user (names, messages, DTMF codes) is always
    written in double quotes, so `123`, `yes` or `A, B` stay text and an
    empty value reads as "" (empty text) – null means "not set" instead."""
    return DoubleQuotedScalarString(value or "")


def mhz(hz: int | None) -> float | None:
    """Hz → MHz, rounded to the 10 Hz resolution radios store."""
    return None if hz is None else round(hz / 1_000_000, 5)


@dataclass
class Channel:
    slot: int  # 1-based position within its zone
    rx: int  # Hz
    tx: int | None  # Hz; None = transmit disabled
    name: str = ""
    mode: str = "FM"  # FM | AM | … – the driver checks what the radio supports
    bw: str = "wide"  # wide | narrow
    power: str = "high"  # driver's level name
    rx_tone: Tone = "off"
    tx_tone: Tone = "off"
    scan: bool = True
    extra: dict[str, Any] = field(default_factory=dict)  # radio-specific, non-default values only

    def to_yaml(self) -> CommentedMap:
        out = CommentedMap()
        out["slot"] = self.slot
        out["name"] = text(self.name)  # always present, so it is clear where to type one
        out["rx"] = mhz(self.rx)
        if self.tx is None:
            out["duplex"] = "off"
        elif self.tx != self.rx:
            diff = self.tx - self.rx
            if abs(diff) <= SPLIT_THRESHOLD_HZ:
                out["offset"] = mhz(diff)
            else:
                out["tx"] = mhz(self.tx)
        out["mode"] = self.mode
        out["bw"] = self.bw
        out["power"] = self.power
        if self.rx_tone != "off":
            out["rx_tone"] = self.rx_tone
        if self.tx_tone != "off":
            out["tx_tone"] = self.tx_tone
        out["scan"] = self.scan
        if self.extra:
            out["extra"] = _flow(self.extra)
        out.fa.set_flow_style()
        return out


@dataclass
class Zone:
    number: int
    size: int  # number of slots in the zone
    first_slot: int  # global slot (1-based) of position 1
    name: str | None = None
    channels: list[Channel] = field(default_factory=list)


@dataclass
class Section:
    """A group of key/value settings with optional per-key comments."""

    values: dict[str, Any] = field(default_factory=dict)
    comments: dict[str, str] = field(default_factory=dict)

    def set(self, key: str, value: Any, comment: str | None = None) -> None:
        self.values[key] = value
        if comment:
            self.comments[key] = comment


@dataclass
class Codeplug:
    driver: str
    model: str | None = None
    alias: str | None = None
    read_at: str | None = None  # ISO timestamp
    image: str | None = None  # .img path relative to the YAML file
    zones: list[Zone] = field(default_factory=list)
    vfo: dict[str, Section] = field(default_factory=dict)
    settings: Section = field(default_factory=Section)
    extra: dict[str, Section | Any] = field(default_factory=dict)  # DTMF, APRS, broadcast …
    notes: list[str] = field(default_factory=list)  # printed as a comment block on top
    path: Path | None = None  # the YAML file it was loaded from (not written)
    zones_given: bool = True  # False: the YAML has no 'zones' key → channels stay as they are

    def image_path(self) -> Path | None:
        """The base .img referenced by the YAML, resolved relative to the YAML file."""
        if not self.image:
            return None
        base = self.path.parent if self.path else Path.cwd()
        return base / self.image

    @property
    def channel_count(self) -> int:
        return sum(len(z.channels) for z in self.zones)

    def to_yaml(self) -> CommentedMap:
        doc = CommentedMap()
        doc["wokitoki"] = FORMAT_VERSION
        radio = CommentedMap()
        radio["driver"] = self.driver
        for key in ("model", "alias", "read_at", "image"):
            value = getattr(self, key)
            if value is not None:
                radio[key] = value
        doc["radio"] = radio
        header = "Radio configuration – wokitoki (format: docs/FORMATS.md).\n"
        header += "".join(f"{line}\n" for line in self.notes)
        doc.yaml_set_start_comment(header)

        zones = CommentedSeq()
        for zone in self.zones:
            z = CommentedMap()
            z["zone"] = zone.number
            if zone.name is not None:
                z["name"] = text(zone.name)
            z["channels"] = CommentedSeq(ch.to_yaml() for ch in zone.channels)
            if not zone.channels:
                z["channels"].fa.set_flow_style()
            last = zone.first_slot + zone.size - 1
            z.yaml_add_eol_comment(
                f"slots {zone.first_slot}–{last}, {len(zone.channels)}/{zone.size} used", "zone"
            )
            zones.append(z)
        doc["zones"] = zones

        if self.vfo:
            vfo = CommentedMap()
            for name, section in self.vfo.items():
                vfo[name] = _section(section, flow=True)
            doc["vfo"] = vfo
        doc["settings"] = _section(self.settings)
        for key, value in self.extra.items():
            doc[key] = _section(value) if isinstance(value, Section) else _plain(value)
        return doc


def _flow(values: dict[str, Any]) -> CommentedMap:
    out = CommentedMap(values)
    out.fa.set_flow_style()
    return out


def _section(section: Section, *, flow: bool = False, indent: int = 2) -> CommentedMap:
    out = CommentedMap()
    scalars = [(k, v) for k, v in section.values.items() if not isinstance(v, (dict, list, Section))]
    width = max((len(k) + len(_scalar_text(v)) for k, v in scalars), default=0)
    column = min(indent + width + 3, 60)
    for key, value in section.values.items():
        out[key] = _section(value) if isinstance(value, Section) else _plain(value)
        if key in section.comments and not flow:
            out.yaml_add_eol_comment(section.comments[key], key, column=column)
    if flow:
        out.fa.set_flow_style()
    return out


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _plain(value: Any) -> Any:
    """Lists of dicts are written one entry per line (flow style)."""
    if isinstance(value, list):
        seq = CommentedSeq(_flow(v) if isinstance(v, dict) else v for v in value)
        if not value:
            seq.fa.set_flow_style()
        return seq
    if isinstance(value, dict):
        return _flow(value)
    return value


def _represent_none(representer, _data):
    # ruamel writes None in block mappings as an empty value ("sql:"); spell it out.
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def _yaml() -> YAML:
    y = YAML()
    y.representer.add_representer(type(None), _represent_none)
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 200  # keep one channel per line
    return y


def dump(codeplug: Codeplug, stream: TextIO) -> None:
    _yaml().dump(codeplug.to_yaml(), stream)


def save(codeplug: Codeplug, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        dump(codeplug, f)


# --- reading YAML back ---------------------------------------------------------

CHANNEL_KEYS = (
    "slot",
    "name",
    "rx",
    "tx",
    "offset",
    "duplex",
    "mode",
    "bw",
    "power",
    "rx_tone",
    "tx_tone",
    "scan",
    "extra",
)
DCS_RE = re.compile(r"^D(\d{3})([NI])$")


def plain(value: Any) -> Any:
    """ruamel / Section objects → plain dicts, lists, str, int, float, bool, None."""
    if isinstance(value, Section):
        return {k: plain(v) for k, v in value.values.items()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def _mhz_to_hz(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodeplugError(f"{where}: expected a frequency in MHz, got {value!r}")
    return round(float(value) * 1_000_000)


def parse_tone(value: Any, where: str) -> Tone:
    if value is None or value is False or (isinstance(value, str) and value.lower() == "off"):
        return "off"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        hz = round(float(value), 1)
        if not 60 <= hz <= 260:
            raise CodeplugError(f"{where}: CTCSS {value} Hz is out of range (60–260)")
        return hz
    if isinstance(value, str):
        tone = value.strip().upper()
        if DCS_RE.match(tone) or tone.startswith("?"):
            return tone
    raise CodeplugError(
        f"{where}: tone must be off, a CTCSS value like 88.5 or DCS like D023N, got {value!r}"
    )


def _choice(value: Any, allowed: tuple[str, ...], where: str) -> str:
    if value not in allowed:
        raise CodeplugError(f"{where}: {value!r} is not one of {' | '.join(allowed)}")
    return str(value)


def _mode(value: Any, where: str) -> str:
    """Any modulation name; which ones a radio supports is the driver's check."""
    if not isinstance(value, str) or not value.strip():
        raise CodeplugError(f"{where}: expected a modulation like FM or AM, got {value!r}")
    return value


def parse_channel(data: Any, where: str) -> Channel:
    if not isinstance(data, dict):
        raise CodeplugError(f"{where}: expected a mapping like {{slot: 1, rx: 446.00625, …}}")
    unknown = set(map(str, data)) - set(CHANNEL_KEYS)
    if unknown:
        raise CodeplugError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    slot = data.get("slot")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 1:
        raise CodeplugError(f"{where}: 'slot' must be a positive number")
    where = f"{where} (slot {slot})"
    if "rx" not in data:
        raise CodeplugError(f"{where}: missing 'rx'")
    rx = _mhz_to_hz(data["rx"], f"{where} rx")
    given = [k for k in ("tx", "offset", "duplex") if data.get(k) is not None]
    if len(given) > 1:
        raise CodeplugError(f"{where}: use only one of tx / offset / duplex")
    if "duplex" in given:
        if str(data["duplex"]).lower() != "off":
            raise CodeplugError(f"{where}: duplex can only be 'off' (use tx or offset otherwise)")
        tx = None
    elif "offset" in given:
        tx = rx + _mhz_to_hz(data["offset"], f"{where} offset")
    elif "tx" in given:
        tx = _mhz_to_hz(data["tx"], f"{where} tx")
    else:
        tx = rx
    name = data.get("name")
    scan = data.get("scan", True)
    if not isinstance(scan, bool):
        raise CodeplugError(f"{where}: scan must be true or false")
    extra = data.get("extra") or {}
    if not isinstance(extra, dict):
        raise CodeplugError(f"{where}: extra must be a mapping")
    return Channel(
        slot=slot,
        rx=rx,
        tx=tx,
        name="" if name is None else str(name),
        mode=_mode(data.get("mode", "FM"), f"{where} mode"),
        bw=_choice(data.get("bw", "wide"), ("wide", "narrow"), f"{where} bw"),
        power=str(data.get("power", "high")),
        rx_tone=parse_tone(data.get("rx_tone"), f"{where} rx_tone"),
        tx_tone=parse_tone(data.get("tx_tone"), f"{where} tx_tone"),
        scan=scan,
        extra=plain(extra),
    )


def _parse_zone(data: Any, index: int) -> Zone:
    where = f"zones[{index}]"
    if not isinstance(data, dict) or not isinstance(data.get("zone"), int):
        raise CodeplugError(f"{where}: expected a mapping with 'zone: <number>'")
    unknown = set(map(str, data)) - {"zone", "name", "channels"}
    if unknown:
        raise CodeplugError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    number = int(data["zone"])
    channels = data.get("channels") or []
    if not isinstance(channels, list):
        raise CodeplugError(f"zone {number}: 'channels' must be a list")
    zone = Zone(
        number=number, size=0, first_slot=0, name=None if data.get("name") is None else str(data["name"])
    )
    zone.channels = [parse_channel(ch, f"zone {number}, channel {i}") for i, ch in enumerate(channels, 1)]
    seen: set[int] = set()
    for ch in zone.channels:
        if ch.slot in seen:
            raise CodeplugError(f"zone {number}: slot {ch.slot} is used twice")
        seen.add(ch.slot)
    return zone


def load(path: Path) -> Codeplug:
    """Read a configuration YAML written by wokitoki (and possibly edited by hand)."""
    try:
        with path.open(encoding="utf-8") as f:
            doc = YAML().load(f)
    except OSError as e:
        raise CodeplugError(f"cannot read {path}: {e}") from e
    except Exception as e:
        raise CodeplugError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(doc, dict) or doc.get("wokitoki") != FORMAT_VERSION:
        raise CodeplugError(f"{path}: not a wokitoki configuration (missing 'wokitoki: {FORMAT_VERSION}')")
    radio = doc.get("radio") or {}
    if not isinstance(radio, dict) or not radio.get("driver"):
        raise CodeplugError(f"{path}: missing 'radio: {{driver: …}}'")
    zones_given = "zones" in doc
    zones = doc.get("zones") or []
    if not isinstance(zones, list):
        raise CodeplugError(f"{path}: 'zones' must be a list")
    cp = Codeplug(
        driver=str(radio["driver"]),
        model=None if radio.get("model") is None else str(radio["model"]),
        alias=None if radio.get("alias") is None else str(radio["alias"]),
        read_at=None if radio.get("read_at") is None else str(radio["read_at"]),
        image=None if radio.get("image") is None else str(radio["image"]),
        zones=[_parse_zone(z, i) for i, z in enumerate(zones, 1)],
        path=path,
        zones_given=zones_given,
    )
    numbers = [z.number for z in cp.zones]
    if len(numbers) != len(set(numbers)):
        raise CodeplugError(f"{path}: a zone number is used twice")
    vfo = doc.get("vfo") or {}
    cp.vfo = {str(k): Section(values=plain(v)) for k, v in vfo.items()}
    cp.settings = Section(values=plain(doc.get("settings") or {}))
    for key, value in doc.items():
        if key not in ("wokitoki", "radio", "zones", "vfo", "settings"):
            cp.extra[str(key)] = Section(values=plain(value)) if isinstance(value, dict) else plain(value)
    return cp


# --- differences for humans ------------------------------------------------------


def _channel_map(cp: Codeplug) -> dict[tuple[int, int], dict]:
    out = {}
    for zone in cp.zones:
        for ch in zone.channels:
            data = plain(ch.to_yaml())
            data.setdefault("rx_tone", "off")
            data.setdefault("tx_tone", "off")
            out[(zone.number, ch.slot)] = data
    return out


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, list) and value and all(isinstance(v, dict) and "n" in v for v in value):
        for entry in value:  # numbered memories (broadcast.fm, dtmf.groups): one line per memory
            out[f"{prefix}[{entry['n']}]"] = {k: v for k, v in entry.items() if k != "n"}
        return
    if isinstance(value, dict) and value:
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else k, v, out)
    else:
        out[prefix] = value


def _fmt(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) + "}"
    if isinstance(value, str):
        return f'"{value}"' if value == "" or " " in value else value
    return _scalar_text(value)


def diff(old: Codeplug, new: Codeplug) -> list[str]:
    """Human-readable changes from ``old`` to ``new`` (both decoded from images)."""
    lines: list[str] = []
    a, b = _channel_map(old), _channel_map(new)
    for key in sorted(set(a) | set(b)):
        where = f"zone {key[0]} slot {key[1]}"
        if key not in b:
            lines.append(f"- {where}: delete {_fmt(a[key])}")
        elif key not in a:
            lines.append(f"+ {where}: new {_fmt(b[key])}")
        elif a[key] != b[key]:
            fields = sorted(set(a[key]) | set(b[key]), key=list(CHANNEL_KEYS).index)
            changes = [
                f"{f} {_fmt(a[key].get(f))} → {_fmt(b[key].get(f))}"
                for f in fields
                if a[key].get(f) != b[key].get(f)
            ]
            lines.append(f"~ {where} {_fmt(b[key].get('name', ''))}: " + ", ".join(changes))
    old_rest: dict[str, Any] = {}
    new_rest: dict[str, Any] = {}
    for cp, out in ((old, old_rest), (new, new_rest)):
        _flatten("vfo", {k: plain(v) for k, v in cp.vfo.items()}, out)
        _flatten("settings", plain(cp.settings), out)
        for k, v in cp.extra.items():
            _flatten(k, plain(v), out)

    def natural(key: str) -> list:
        return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", key)]

    for key in sorted(set(old_rest) | set(new_rest), key=natural):
        if old_rest.get(key) != new_rest.get(key):
            lines.append(f"~ {key}: {_fmt(old_rest.get(key))} → {_fmt(new_rest.get(key))}")
    return lines
