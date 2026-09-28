"""Radtel RT-950 Pro over Bluetooth LE. See docs/radios/radtel-rt950pro.md."""

from __future__ import annotations

from wokitoki.core.codeplug import Codeplug
from wokitoki.core.driver import Capabilities, Driver, Progress, RadioInfo
from wokitoki.core.transport import BleProfile, BleUnlock, Transport, hexdump, uuid16

from . import rt950pro_map
from .common import (
    CloneSession,
    HandshakeSpec,
    Segment,
    WriteArea,
    close_session,
    open_session,
    quick_spec,
    read_segments,
    write_areas,
)

UNLOCK_PAYLOAD = bytes.fromhex("3F3F3F3F022E171D5E57252F57136256044B2342")

# Clone layout in image order; the .img is these segments concatenated.
# Unread (never write!): 0x7800–0x7FFF, 0xC000–0xCFFF, 0xD300–0xFFFF (0xE000+ calibration).
SEGMENTS = (
    Segment(0x52, 0x57, 0x0000, 0x7800, "channels"),
    Segment(0x52, 0x57, 0x8000, 0x0100, "VFO"),
    Segment(0x52, 0x57, 0x9000, 0x0100, "functions"),
    Segment(0x52, 0x57, 0xA000, 0x0200, "DTMF"),
    Segment(0x52, 0x57, 0xB000, 0x0200, "FM/AM/SSB parameters"),
    Segment(0x52, 0x57, 0xD000, 0x0300, "FM/AM/SSB names"),
    Segment(0x54, 0x58, 0x0000, 0x0080, "APRS"),
)


# Write layout of the RT950Pro app (verified there): same segments as reading,
# pacing per segment, the APRS block last – it commits to flash and the radio
# then drops the BLE link.
WRITE_AREAS = (
    WriteArea(0x57, 0x0000, 0x7800, 0x0000, "channels", ack_timeout=8.0),
    WriteArea(0x57, 0x8000, 0x0100, 0x7800, "VFO", ack_timeout=10.0, post_delay=0.05),
    WriteArea(0x57, 0x9000, 0x0100, 0x7900, "functions", ack_timeout=10.0, post_delay=0.05),
    WriteArea(0x57, 0xA000, 0x0200, 0x7A00, "DTMF", ack_timeout=10.0, post_delay=0.05),
    WriteArea(0x57, 0xB000, 0x0200, 0x7C00, "FM/AM/SSB parameters", ack_timeout=10.0, post_delay=0.05),
    WriteArea(0x57, 0xD000, 0x0300, 0x7E00, "FM/AM/SSB names", ack_timeout=10.0, post_delay=0.05),
    WriteArea(0x58, 0x0000, 0x0080, 0x8100, "APRS (commit)", ack_timeout=30.0, post_delay=0.1, commit=True),
)


class RT950Pro(Driver):
    id = "radtel-rt950pro"
    vendor = "Radtel"
    model = "RT-950 Pro"
    ble = BleProfile(
        service_uuid=uuid16("FFE0"),
        data_uuid=uuid16("FFE1"),
        unlock=BleUnlock(uuid=uuid16("FF31"), payload=UNLOCK_PAYLOAD, settle=0.4, reply_prefix=b"\x21"),
        chunk_size=20,
        name_hints=("RT-950", "RT950", "950"),
    )
    # USB cable protocol not verified yet – BLE only for now.
    serial = None
    capabilities = Capabilities(
        channels=rt950pro_map.CHANNEL_COUNT,
        zones=rt950pro_map.ZONE_COUNT,
        channels_per_zone=rt950pro_map.CHANNELS_PER_ZONE,
        name_length=12,
        power_levels=rt950pro_map.POWER_LEVELS,
        power_watts=None,  # not known for the RT-950 Pro (max. ~10 W per Radtel)
        fm_slots=rt950pro_map.FM_SLOTS,
        fm_name_length=12,
    )
    image_size = rt950pro_map.IMAGE_SIZE

    handshake = HandshakeSpec(
        magic=b"PROGRAMBT9000U",
        ident_length=16,
        model_length=12,
        known_idents=(bytes.fromhex("0136017404000520"),),  # our unit (VERIFIED)
    )

    async def identify(self, t: Transport, *, quick: bool = False) -> RadioInfo:
        session = await open_session(t, quick_spec(self.handshake) if quick else self.handshake)
        await close_session(t)
        self.info = self._info(session)
        return self.info

    async def read_image(self, t: Transport, progress: Progress | None = None) -> bytes:
        image, session = await read_segments(t, self.handshake, SEGMENTS, progress)
        self.info = self._info(session)
        self.check_image(image)
        return image

    @staticmethod
    def _info(session: CloneSession) -> RadioInfo:
        return RadioInfo(model=session.model, ident=session.ident, details={"ident": hexdump(session.ident)})

    async def write_image(self, t: Transport, image: bytes, progress: Progress | None = None) -> None:
        self.check_image(image)
        await write_areas(t, self.handshake, WRITE_AREAS, image, progress)

    def writable_ranges(self) -> list[tuple[int, int]]:
        return [area.image_range() for area in WRITE_AREAS]

    def encode(self, cp: Codeplug, base: bytes) -> bytes:
        self.check_image(base)
        return rt950pro_map.encode_image(cp, base)

    def decode(self, image: bytes) -> Codeplug:
        self.check_image(image)
        return rt950pro_map.decode_image(image, self.id)
