"""Quansheng UV-K5 with the F4HWN firmware v4.x – programming cable only.

Protocol and memory map from the F4HWN CHIRP driver (4.2.0); reading verified
on our radio (F4HWN v4.2). See docs/radios/quansheng-uvk5-f4hwn.md.
"""

from __future__ import annotations

import logging

from wokitoki.core.codeplug import Codeplug
from wokitoki.core.driver import Capabilities, Driver, DriverError, Progress, RadioInfo
from wokitoki.core.transport import SerialProfile, Transport

from . import protocol, uvk5_map

log = logging.getLogger("wokitoki.driver.quansheng")

# The memory map is the one of F4HWN v4.x; other firmware (stock, egzumer,
# F4HWN v5 for the K5 V3 / K1) stores things elsewhere.
SUPPORTED_FIRMWARE = ("F4HWN v4.",)


class UVK5F4HWN(Driver):
    id = "quansheng-uvk5-f4hwn"
    vendor = "Quansheng"
    model = "UV-K5 (F4HWN v4)"
    ble = None
    serial = SerialProfile(baudrate=38400)
    capabilities = Capabilities(
        channels=uvk5_map.CHANNEL_COUNT,
        zones=0,
        channels_per_zone=0,
        name_length=10,
        power_levels=uvk5_map.POWER_LEVELS,
        power_watts={"high": 5, "mid": 2, "low5": 1, "low4": 0.5, "low3": 0.25, "low2": 0.125, "low1": 0.02},
        fm_slots=uvk5_map.FM_SLOTS,
        fm_name_length=0,
        rx_only_extra={"tx_lock": True},  # duplex off does not exist on the K5
    )
    image_size = uvk5_map.IMAGE_SIZE

    @staticmethod
    def supported(firmware: str) -> bool:
        return firmware.startswith(SUPPORTED_FIRMWARE)

    async def _hello(self, t: Transport, timeout: float = 2.0) -> str:
        firmware = (await protocol.hello(t, timeout)).firmware
        self.info = RadioInfo(
            model="UV-K5",
            firmware=firmware,
            details={"firmware_supported": self.supported(firmware)},
        )
        return firmware

    async def identify(self, t: Transport, *, quick: bool = False) -> RadioInfo:
        firmware = await self._hello(t, 1.0 if quick else 2.0)
        if not self.supported(firmware):
            log.warning("firmware %r is not F4HWN v4.x – the memory map may not match", firmware)
        return self.info

    async def read_image(self, t: Transport, progress: Progress | None = None) -> bytes:
        firmware = await self._hello(t)
        if not self.supported(firmware):
            log.warning("firmware %r is not F4HWN v4.x – decoding may be wrong", firmware)
        image = await protocol.read_range(t, 0, uvk5_map.IMAGE_SIZE, progress)
        self.check_image(image)
        return image

    async def write_image(self, t: Transport, image: bytes, progress: Progress | None = None) -> None:
        self.check_image(image)
        firmware = await self._hello(t)
        if not self.supported(firmware):
            raise DriverError(f"refusing to write: firmware {firmware!r} is not F4HWN v4.x")
        blocks = [
            (offset, min(protocol.BLOCK, end - offset))
            for start, end in uvk5_map.WRITABLE
            for offset in range(start, end, protocol.BLOCK)
        ]
        for n, (offset, length) in enumerate(blocks, 1):
            await protocol.write_block(t, offset, image[offset : offset + length])
            if progress:
                progress(n, len(blocks))
        # Like CHIRP: reboot so the firmware loads the new EEPROM contents.
        await protocol.reset(t)

    def writable_ranges(self) -> list[tuple[int, int]]:
        return list(uvk5_map.WRITABLE)

    def decode(self, image: bytes) -> Codeplug:
        self.check_image(image)
        return uvk5_map.decode_image(image, self.id)

    def encode(self, cp: Codeplug, base: bytes) -> bytes:
        self.check_image(base)
        return uvk5_map.encode_image(cp, base)
