"""Radtel RT-900 (BT) – BLE or programming cable. See docs/radios/radtel-rt900.md.

Protocol and memory map follow CHIRP (radtel_rt900.py, RT900BT, FW V1.20P);
reading and writing verified on hardware over BLE and the USB cable.
"""

from __future__ import annotations

from wokitoki.core.codeplug import Codeplug
from wokitoki.core.driver import Capabilities, Driver, Progress, RadioInfo
from wokitoki.core.transport import BleProfile, BleUnlock, SerialProfile, Transport, hexdump, uuid16

from . import rt900_map
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
from .rt950pro import UNLOCK_PAYLOAD

# Fixed SEND frame used by CHIRP; selects XOR key "CO 7" (CHIRP: _crypt(1, …)).
SEND_FRAME = b"SEND \x01\x01" + b"\x00" * 18

# Whole address space like CHIRP (0xF000+ is calibration: plain text, never write).
SEGMENTS = (
    Segment(0x52, 0x57, 0x0000, rt900_map.IMAGE_SIZE, "whole memory", block_size=0x40, plain_from=0xF000),
)


# What CHIRP writes (RT900BT._ranges), in 64 B blocks – except 0xD000 (hidden
# radio mode, read only for us). Never calibration 0xF000+ and never the
# unmapped 0xE000–0xEFFF area the radio writes by itself.
WRITE_AREAS = (
    WriteArea(0x57, 0x0000, 0x7CE0, 0x0000, "channels", block_size=0x40, ack_timeout=3.0),
    WriteArea(0x57, 0x8000, 0x0040, 0x8000, "VFO", block_size=0x40, ack_timeout=3.0),
    WriteArea(0x57, 0x9000, 0x0040, 0x9000, "settings", block_size=0x40, ack_timeout=3.0),
    WriteArea(0x57, 0xA000, 0x0140, 0xA000, "DTMF", block_size=0x40, ack_timeout=3.0),
)


class RT900(Driver):
    id = "radtel-rt900"
    vendor = "Radtel"
    model = "RT-900"
    # Assumed to be the same HM-10 style module as the RT-950 Pro (CHIRP talks
    # to it through ble-serial). The unlock is only written if FF31 exists.
    ble = BleProfile(
        service_uuid=uuid16("FFE0"),
        data_uuid=uuid16("FFE1"),
        unlock=BleUnlock(uuid=uuid16("FF31"), payload=UNLOCK_PAYLOAD, settle=0.4, reply_prefix=b"\x21"),
        chunk_size=20,
        name_hints=("RT-900", "RT900"),
    )
    # VERIFIED 2026-09-26 with a CH340 programming cable (Kenwood 2-pin plug).
    serial = SerialProfile(baudrate=57600)
    capabilities = Capabilities(
        channels=rt900_map.CHANNEL_COUNT,
        zones=0,
        channels_per_zone=0,
        name_length=12,
        power_levels=rt900_map.POWER_LEVELS,
        power_watts={"high": 8, "mid": 4, "low": 1},  # CHIRP
    )
    image_size = rt900_map.IMAGE_SIZE

    handshake = HandshakeSpec(
        magic=b"PROGRAMBT80U",
        ident_length=16,  # CHIRP: 8 B after "F", then 8 B secondary ident
        model_length=None,  # no "M" command
        send_frame=SEND_FRAME,
        known_idents=(bytes.fromhex("0136018004000520"), bytes.fromhex("0100018004000520")),
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

    def _info(self, session: CloneSession) -> RadioInfo:
        known = any(session.ident.startswith(k) for k in self.handshake.known_idents)
        return RadioInfo(
            model=self.model,
            ident=session.ident,
            details={"ident": hexdump(session.ident), "ident_known": known},
        )

    async def write_image(self, t: Transport, image: bytes, progress: Progress | None = None) -> None:
        self.check_image(image)
        await write_areas(t, self.handshake, WRITE_AREAS, image, progress)

    def writable_ranges(self) -> list[tuple[int, int]]:
        return [area.image_range() for area in WRITE_AREAS]

    def encode(self, cp: Codeplug, base: bytes) -> bytes:
        self.check_image(base)
        return rt900_map.encode_image(cp, base)

    def decode(self, image: bytes) -> Codeplug:
        self.check_image(image)
        return rt900_map.decode_image(image, self.id)
