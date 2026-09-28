"""Default configuration profiles (docs/FORMATS.md, section 5).

A profile is a per-country, per-radio template: which channel libraries go
to which zone/slots and with which power, which settings to set and which FM
broadcast library fills the radio's FM memories. It is applied on top of the
radio's current configuration, so everything the profile does not mention
(unknown bytes, other settings, calibration) stays as it is.

Built-in profiles ship in ``wokitoki/profiles/<country>/<driver>.yaml``, user
profiles live in ``<config>/profiles/`` and override them by id.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import paths
from .codeplug import Codeplug, CodeplugError, Section, parse_channel
from .library import get_library

BLOCK_KEYS = {
    "zone",
    "start",
    "library",
    "count",
    "power",
    "power_by_limit",
    "tx",
    "scan",
    "bw",
    "mode",
    "extra",
}
PROFILE_KEYS = {
    "profile",
    "title",
    "country",
    "driver",
    "notes",
    "channels",
    "blocks",
    "settings",
    "broadcast",
}


class ProfileError(Exception):
    pass


@dataclass
class Block:
    """Channels of one library placed from ``start`` in ``zone``."""

    library: str
    zone: int = 1
    start: int = 1
    count: int | None = None
    power: str | None = None
    power_by_limit: dict[float, str] | None = None  # legal limit in W → power level
    tx: str | None = None  # "on" | "off" (receive only) | None = the library's legal status decides
    scan: bool | None = None
    bw: str | None = None
    mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Profile:
    id: str
    title: str
    driver: str
    path: Path
    builtin: bool
    country: str | None = None
    notes: list[str] = field(default_factory=list)
    channels: str = "replace"  # replace: the profile's channels only; merge: keep other slots
    blocks: list[Block] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    fm_library: str | None = None
    fm_names: bool = True


def builtin_profile_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "profiles"


def user_profile_dir() -> Path:
    return paths.config_dir() / "profiles"


def _load(path: Path, root: Path, builtin: bool) -> Profile:
    pid = path.relative_to(root).with_suffix("").as_posix()
    try:
        with path.open(encoding="utf-8") as f:
            doc = YAML(typ="safe").load(f)
    except (OSError, YAMLError) as e:
        raise ProfileError(f"{path}: {e}") from e
    if not isinstance(doc, dict) or not doc.get("driver"):
        raise ProfileError(f"{path}: expected a mapping with 'driver'")
    if doc.get("profile", pid) != pid:
        raise ProfileError(f"{path}: 'profile: {doc['profile']}' does not match the file location ({pid})")
    unknown = set(doc) - PROFILE_KEYS
    if unknown:
        raise ProfileError(f"{path}: unknown keys {', '.join(sorted(unknown))}")
    if doc.get("channels", "replace") not in ("replace", "merge"):
        raise ProfileError(f"{path}: channels must be replace or merge")
    blocks = []
    for i, data in enumerate(doc.get("blocks") or [], 1):
        if not isinstance(data, dict) or "library" not in data:
            raise ProfileError(f"{path}: block {i} needs 'library'")
        bad = set(data) - BLOCK_KEYS
        if bad:
            raise ProfileError(f"{path}: block {i}: unknown keys {', '.join(sorted(bad))}")
        limits = data.get("power_by_limit")
        where = f"{path}: block {i}"
        if limits is not None and not isinstance(limits, dict):
            raise ProfileError(f"{where}: power_by_limit must be a mapping like {{0.5: low, 5: high}}")
        blocks.append(
            Block(
                library=str(data["library"]),
                zone=_int(data.get("zone", 1), f"{where} zone", 1),
                start=_int(data.get("start", 1), f"{where} start", 1),
                count=None if data.get("count") is None else _int(data["count"], f"{where} count", 1),
                power=data.get("power"),
                power_by_limit=None if limits is None else {float(k): str(v) for k, v in limits.items()},
                tx=_tx_value(data.get("tx"), where),
                scan=data.get("scan"),
                bw=data.get("bw"),
                mode=data.get("mode"),
                extra=dict(data.get("extra") or {}),
            )
        )
    broadcast = doc.get("broadcast") or {}
    fm = broadcast.get("fm") or {}
    return Profile(
        id=pid,
        title=str(doc.get("title") or pid),
        driver=str(doc["driver"]),
        path=path,
        builtin=builtin,
        country=doc.get("country"),
        notes=[str(n) for n in doc.get("notes") or []],
        channels=doc.get("channels", "replace"),
        blocks=blocks,
        settings=dict(doc.get("settings") or {}),
        fm_library=fm.get("library"),
        fm_names=bool(fm.get("names", True)),
    )


def profiles() -> dict[str, Profile]:
    found: dict[str, Profile] = {}
    for root, builtin in ((builtin_profile_dir(), True), (user_profile_dir(), False)):
        if root.is_dir():
            for path in sorted(root.rglob("*.yaml")):
                prof = _load(path, root, builtin)
                found[prof.id] = prof
    return dict(sorted(found.items()))


def get_profile(pid: str) -> Profile:
    found = profiles()
    if pid not in found:
        raise ProfileError(f"unknown profile '{pid}' (known: {', '.join(found) or 'none'})")
    return found[pid]


# Libraries whose channels are imported receive only unless TX is switched on
# explicitly (block `tx: on` or `--tx on`): PMR446 needs ≤ 0.5 W ERP and a
# fixed antenna (typical handhelds do not qualify), amateur bands a licence.
RX_ONLY_BY_DEFAULT = ("restricted", "licence", "rx-only")
TX_MODES = ("profile", "on", "off")


def _int(value, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(f"{where}: expected a whole number ≥ {minimum}, got {value!r}")
    return value


def _tx_value(value, where: str) -> str | None:
    if value is None:
        return None
    if value is True or value == "on":
        return "on"
    if value is False or value == "off":
        return "off"
    raise ProfileError(f"{where}: tx must be on or off, got {value!r}")


def decide_tx(block: Block, legal_tx: str, mode: str, tx_on: frozenset[str] = frozenset()) -> bool:
    """Whether the block's channels may transmit: a library named in --tx-on,
    then the global --tx mode, then the block, then the library's legal status."""
    if mode not in TX_MODES:
        raise ProfileError(f"--tx must be one of {', '.join(TX_MODES)}")
    wanted = {"on": True, "off": False}.get(mode) if mode != "profile" else None
    if block.library in tx_on:
        if legal_tx == "rx-only":
            raise ProfileError(f"{block.library} is receive only – TX cannot be switched on")
        return True
    if wanted is None and block.tx is not None:
        wanted = block.tx == "on"
    if wanted is None:
        wanted = legal_tx not in RX_ONLY_BY_DEFAULT
    if wanted and legal_tx == "rx-only":
        if mode == "on":
            return False  # a global "on" never overrides receive-only frequencies
        raise ProfileError(f"{block.library} is receive only – TX cannot be switched on")
    return wanted


def _channel(block: Block, lib_channel: dict, legal_limit: float | None, slot: int, where: str,
             tx_on: bool, rx_only_extra: dict | None):  # fmt: skip
    data = {k: v for k, v in lib_channel.items() if k != "max_power_w"}
    data["slot"] = slot
    if block.power_by_limit is not None:
        limit = lib_channel.get("max_power_w", legal_limit)
        if limit is None or float(limit) not in block.power_by_limit:
            raise ProfileError(f"{where}: no power level for a {limit} W limit in power_by_limit")
        data["power"] = block.power_by_limit[float(limit)]
    elif block.power is not None:
        data["power"] = block.power
    for key in ("scan", "bw", "mode"):
        if getattr(block, key) is not None:
            data[key] = getattr(block, key)
    if block.extra:
        data["extra"] = {**(data.get("extra") or {}), **block.extra}
    if not tx_on:
        if rx_only_extra:  # e.g. the UV-K5: frequencies stay, a TX lock flag is set
            data["extra"] = {**(data.get("extra") or {}), **rx_only_extra}
        else:  # radios that store "no TX" as duplex off
            data.pop("tx", None)
            data.pop("offset", None)
            data["duplex"] = "off"
    elif rx_only_extra and data.get("extra"):
        data["extra"] = {k: v for k, v in data["extra"].items() if k not in rx_only_extra}
    try:
        return parse_channel(data, where)
    except CodeplugError as e:
        raise ProfileError(str(e)) from None


def build(profile: Profile, current: Codeplug, *, fm_library: str | None = None, merge: bool | None = None,
          fm_slots: int = 0, fm_name_length: int = 0, tx: str = "profile", tx_on: frozenset[str] = frozenset(),
          rx_only_extra: dict | None = None, warnings: list[str] | None = None) -> Codeplug:  # fmt: skip
    """The radio's decoded configuration with the profile applied on top.

    ``tx``: "profile" (blocks and library legal status decide), "off" (every
    imported channel receive only) or "on" (TX wherever the library allows it
    at all – never on receive-only libraries); ``tx_on`` switches TX on only
    for the named libraries. ``warnings`` collects notes about TX switched on
    for restricted/licensed channels or against the profile."""
    unknown = set(tx_on) - {b.library for b in profile.blocks}
    if unknown:
        raise ProfileError(f"--tx-on: the profile does not use {', '.join(sorted(unknown))}")
    cp = copy.deepcopy(current)
    zones = {z.number: z for z in cp.zones}
    if not (merge if merge is not None else profile.channels == "merge"):
        for zone in zones.values():
            zone.channels = []
    for i, block in enumerate(profile.blocks, 1):
        where = f"{profile.id} block {i} ({block.library})"
        lib = get_library(block.library)
        if lib.kind != "channels":
            raise ProfileError(f"{where}: {block.library} is not a channel library")
        if block.zone not in zones:
            raise ProfileError(f"{where}: the radio has no zone {block.zone}")
        zone = zones[block.zone]
        chosen = lib.channels if block.count is None else lib.channels[: block.count]
        if block.start + len(chosen) - 1 > zone.size:
            raise ProfileError(
                f"{where}: {len(chosen)} channels from slot {block.start} do not fit in {zone.size}"
            )
        block_tx = decide_tx(block, lib.tx, tx, tx_on)
        if block_tx and warnings is not None:
            note = " ".join(str(lib.legal.get("note", "")).split())
            if lib.tx in ("restricted", "licence"):
                warnings.append(
                    f"TX is ON for {block.library} ({lib.tx}) – at your own responsibility. {note}"
                )
            elif block.tx == "off":
                warnings.append(
                    f"TX is ON for {block.library} although the profile makes it receive only "
                    f"(see `wokitoki profile show {profile.id}`) – at your own responsibility."
                )
        placed = {
            block.start + n: _channel(
                block, ch, lib.legal.get("max_power_w"), block.start + n, where, block_tx, rx_only_extra
            )
            for n, ch in enumerate(chosen)
        }
        zone.channels = sorted(
            [c for c in zone.channels if c.slot not in placed] + list(placed.values()), key=lambda c: c.slot
        )
    for key, value in profile.settings.items():
        if key not in cp.settings.values:
            raise ProfileError(f"{profile.id}: the radio has no setting '{key}'")
        cp.settings.values[key] = value
    library = fm_library or profile.fm_library
    if library:
        if not fm_slots:
            raise ProfileError(f"{profile.driver} has no FM broadcast memories")
        lib = get_library(library)
        if lib.kind != "broadcast" or lib.band != "fm":
            raise ProfileError(f"{library} is not an FM broadcast library")
        stations = sorted(lib.stations[:fm_slots], key=lambda s: s["freq"])  # the strongest, by frequency
        entries = []
        for n, st in enumerate(stations, 1):
            entry: dict[str, Any] = {"n": n, "freq": float(st["freq"])}
            if fm_name_length:
                entry["name"] = st["name"][:fm_name_length] if profile.fm_names else ""
            entries.append(entry)
        broadcast = cp.extra.get("broadcast")
        if not isinstance(broadcast, Section):
            raise ProfileError(f"{profile.driver}: no broadcast section to fill")
        broadcast.values["fm"] = entries
    cp.notes = [
        *cp.notes,
        "",
        f"Built from profile {profile.id}" + (f" with {library}" if library else "") + ".",
    ]
    return cp
