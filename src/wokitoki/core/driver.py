"""Driver interface and registry.

Built-in drivers are always available; external packages register theirs
under the ``wokitoki.drivers`` entry point group (see docs/DRIVERS.md).
"""

from __future__ import annotations

import logging
from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar

from .transport import BleAdvert, BleProfile, SerialPortInfo, SerialProfile, Transport

if TYPE_CHECKING:
    from .codeplug import Codeplug

log = logging.getLogger("wokitoki.driver")

ENTRY_POINT_GROUP = "wokitoki.drivers"

BUILTIN_DRIVERS = {
    "radtel-rt950pro": "wokitoki.drivers.radtel.rt950pro:RT950Pro",
    "radtel-rt900": "wokitoki.drivers.radtel.rt900:RT900",
    "quansheng-uvk5-f4hwn": "wokitoki.drivers.quansheng.uvk5:UVK5F4HWN",
}

Progress = Callable[[int, int], None]  # (done, total)


class DriverError(Exception):
    """The radio answered, but not the way the driver expects."""


@dataclass
class RadioInfo:
    model: str
    firmware: str | None = None
    ident: bytes | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    channels: int = 0
    zones: int = 0
    channels_per_zone: int = 0
    name_length: int = 0
    power_levels: tuple[str, ...] = ()
    power_watts: dict[str, float] | None = None  # nominal output per level, if known
    fm_slots: int = 0  # FM broadcast memories
    fm_name_length: int = 0  # 0 = the radio cannot name FM memories
    # How a receive-only channel is stored: None = duplex off (no TX frequency /
    # TX disabled flag); a dict = these channel extras instead (UV-K5: tx_lock).
    rx_only_extra: dict[str, Any] | None = None


class Driver(ABC):
    id: ClassVar[str]
    vendor: ClassVar[str]
    model: ClassVar[str]
    ble: ClassVar[BleProfile | None] = None
    serial: ClassVar[SerialProfile | None] = None
    capabilities: ClassVar[Capabilities] = Capabilities()
    image_size: ClassVar[int] = 0  # bytes in a full memory image (.img)

    def __init__(self) -> None:
        self.info: RadioInfo | None = None  # filled by identify() / read_image()

    @classmethod
    def transports(cls) -> set[str]:
        kinds = set()
        if cls.ble is not None:
            kinds.add("ble")
        if cls.serial is not None:
            kinds.add("serial")
        return kinds

    @classmethod
    def match_ble(cls, advert: BleAdvert) -> int:
        """0–100: how much the advertised device looks like this radio."""
        if cls.ble is None:
            return 0
        score = 0
        if cls.ble.service_uuid.lower() in advert.service_uuids:
            score = 60
        name = (advert.name or "").upper()
        if name and any(hint.upper() in name for hint in cls.ble.name_hints):
            score += 30
        return min(score, 100)

    @classmethod
    def match_serial(cls, port: SerialPortInfo) -> int:
        """0–100: how much the serial port looks like this radio's cable."""
        if cls.serial is None or port.vid is None:
            return 0
        return 30 if (port.vid, port.pid) in cls.serial.usb_ids else 0

    async def identify(self, t: Transport, *, quick: bool = False) -> RadioInfo:
        """Handshake, learn model/firmware, then leave clone mode again.

        ``quick``: one short attempt, used by ``scan`` to probe cables where a
        failure is the normal answer ("not this radio")."""
        raise NotImplementedError(f"{self.id}: identify is not implemented yet")

    async def read_image(self, t: Transport, progress: Progress | None = None) -> bytes:
        raise NotImplementedError(f"{self.id}: reading is not implemented yet")

    async def write_image(self, t: Transport, image: bytes, progress: Progress | None = None) -> None:
        raise NotImplementedError(f"{self.id}: writing is not implemented yet")

    def decode(self, image: bytes) -> Codeplug:
        """Memory image → radio-independent model (radio fields left to the caller)."""
        raise NotImplementedError(f"{self.id}: decoding is not implemented yet")

    def encode(self, cp: Codeplug, base: bytes) -> bytes:
        """Patch ``base`` so that it holds ``cp``; unknown bytes stay untouched."""
        raise NotImplementedError(f"{self.id}: encoding is not implemented yet")

    def writable_ranges(self) -> list[tuple[int, int]]:
        """Image offset ranges [start, end) that write_image sends to the radio."""
        return []

    def check_write(self, current: bytes, target: bytes) -> list[int]:
        """Offsets that differ; refuses any change outside the writable ranges."""
        self.check_image(current)
        self.check_image(target)
        changed = [i for i, (a, b) in enumerate(zip(current, target, strict=True)) if a != b]
        ranges = self.writable_ranges()
        outside = [i for i in changed if not any(lo <= i < hi for lo, hi in ranges)]
        if outside:
            raise DriverError(
                f"{self.id}: refusing to write – {len(outside)} changed bytes lie outside the writable "
                f"areas (first at image offset 0x{outside[0]:04X})"
            )
        return changed

    def check_image(self, image: bytes) -> None:
        if self.image_size and len(image) != self.image_size:
            raise DriverError(f"{self.id}: invalid image size {len(image)} B (expected {self.image_size} B)")


_registry: dict[str, type[Driver]] | None = None


def _load(target: str) -> type[Driver]:
    module, _, attr = target.partition(":")
    return getattr(import_module(module), attr)


def drivers() -> dict[str, type[Driver]]:
    """All known drivers by id (built-in first, then entry points)."""
    global _registry
    if _registry is None:
        found: dict[str, type[Driver]] = {}
        for driver_id, target in BUILTIN_DRIVERS.items():
            found[driver_id] = _load(target)
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if ep.name in found:
                continue
            try:
                found[ep.name] = ep.load()
            except Exception as e:  # noqa: BLE001 – a broken plugin must not break the CLI
                log.warning("cannot load driver %s (%s): %s", ep.name, ep.value, e)
        _registry = dict(sorted(found.items()))
    return _registry


def get_driver(driver_id: str) -> type[Driver]:
    try:
        return drivers()[driver_id]
    except KeyError:
        known = ", ".join(drivers()) or "none"
        raise KeyError(f"unknown driver '{driver_id}' (known: {known})") from None
