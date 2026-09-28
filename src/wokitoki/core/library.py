"""Channel libraries (docs/FORMATS.md, section 4).

Built-in libraries ship in ``wokitoki/library/<country>/<name>.yaml``, user
libraries live in ``<config>/library/`` with the same layout; a user library
with the same id overrides the built-in one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import paths

LEGAL_TX = ("allowed", "restricted", "licence", "rx-only")
KINDS = ("channels", "broadcast")
STATION_KEYS = {"name", "freq", "program", "transmitter", "erp_kw", "distance_km"}
BANDS = {"fm": (64.0, 108.0), "am": (0.1, 30.0)}  # MHz
CHANNEL_KEYS = {
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
    "max_power_w",
}


class LibraryError(Exception):
    pass


@dataclass
class Library:
    id: str  # "cz/pmr446"
    title: str
    path: Path
    builtin: bool
    country: str | None = None
    source: str | None = None
    legal: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    channels: list[dict[str, Any]] = field(default_factory=list)  # defaults already applied
    kind: str = "channels"  # channels | broadcast
    band: str | None = None  # broadcast: fm | am
    stations: list[dict[str, Any]] = field(default_factory=list)  # broadcast, strongest first

    @property
    def tx(self) -> str:
        return str(self.legal.get("tx", "?"))


def _load(path: Path, root: Path, builtin: bool) -> Library:
    lib_id = path.relative_to(root).with_suffix("").as_posix()
    try:
        with path.open(encoding="utf-8") as f:
            doc = YAML(typ="safe").load(f)
    except (OSError, YAMLError) as e:
        raise LibraryError(f"{path}: {e}") from e
    if not isinstance(doc, dict):
        raise LibraryError(f"{path}: expected a mapping")
    if doc.get("library", lib_id) != lib_id:
        raise LibraryError(f"{path}: 'library: {doc['library']}' does not match the file location ({lib_id})")
    kind = doc.get("kind", "channels")
    if kind not in KINDS:
        raise LibraryError(f"{path}: kind must be one of {', '.join(KINDS)}")
    if kind == "broadcast":
        return _load_broadcast(doc, path, lib_id, builtin)
    if not isinstance(doc.get("channels"), list):
        raise LibraryError(f"{path}: expected a 'channels' list")
    legal = doc.get("legal") or {}
    if legal.get("tx", "allowed") not in LEGAL_TX:
        raise LibraryError(f"{path}: legal.tx must be one of {', '.join(LEGAL_TX)}")
    defaults = doc.get("defaults") or {}
    channels = []
    for i, ch in enumerate(doc["channels"], 1):
        if not isinstance(ch, dict) or "rx" not in ch:
            raise LibraryError(f"{path}: channel {i} needs at least 'rx'")
        unknown = set(ch) - CHANNEL_KEYS
        if unknown:
            raise LibraryError(f"{path}: channel {i}: unknown keys {', '.join(sorted(unknown))}")
        merged = {**defaults, **ch}
        merged["name"] = "" if merged.get("name") is None else str(merged["name"])
        channels.append(merged)
    return Library(
        id=lib_id,
        title=str(doc.get("title") or lib_id),
        path=path,
        builtin=builtin,
        country=doc.get("country"),
        source=None if doc.get("source") is None else " ".join(str(doc["source"]).split()),
        legal=legal,
        defaults=defaults,
        channels=channels,
    )


def _load_broadcast(doc: dict, path: Path, lib_id: str, builtin: bool) -> Library:
    band = doc.get("band", "fm")
    if band not in BANDS:
        raise LibraryError(f"{path}: band must be one of {', '.join(BANDS)}")
    if not isinstance(doc.get("stations"), list):
        raise LibraryError(f"{path}: expected a 'stations' list")
    lo, hi = BANDS[band]
    stations = []
    for i, st in enumerate(doc["stations"], 1):
        if not isinstance(st, dict) or not isinstance(st.get("freq"), int | float):
            raise LibraryError(f"{path}: station {i} needs 'freq' (MHz)")
        unknown = set(st) - STATION_KEYS
        if unknown:
            raise LibraryError(f"{path}: station {i}: unknown keys {', '.join(sorted(unknown))}")
        if not lo <= st["freq"] <= hi:
            raise LibraryError(f"{path}: station {i}: {st['freq']} MHz is outside the {band} band")
        stations.append({**st, "name": "" if st.get("name") is None else str(st["name"])})
    return Library(
        id=lib_id,
        title=str(doc.get("title") or lib_id),
        path=path,
        builtin=builtin,
        country=doc.get("country"),
        source=None if doc.get("source") is None else " ".join(str(doc["source"]).split()),
        legal={"tx": "rx-only"},
        kind="broadcast",
        band=band,
        stations=stations,
    )


def _roots() -> list[tuple[Path, bool]]:
    return [(paths.builtin_library_dir(), True), (paths.user_library_dir(), False)]


def libraries() -> dict[str, Library]:
    """All libraries by id; user libraries override built-in ones."""
    found: dict[str, Library] = {}
    for root, builtin in _roots():
        if root.is_dir():
            for path in sorted(root.rglob("*.yaml")):
                lib = _load(path, root, builtin)
                found[lib.id] = lib
    return dict(sorted(found.items()))


def get_library(lib_id: str) -> Library:
    libs = libraries()
    if lib_id not in libs:
        known = ", ".join(libs) or "none"
        raise LibraryError(f"unknown library '{lib_id}' (known: {known})")
    return libs[lib_id]
