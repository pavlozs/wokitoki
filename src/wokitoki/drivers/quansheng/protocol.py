"""Quansheng UV-K5 serial protocol (stock firmware and its egzumer / F4HWN forks).

Taken from the F4HWN CHIRP driver (armel/uv-k5-chirp-driver, uvk5_egzumer_f4hwn
4.2.0). A frame is ``AB CD`` + length (u16 LE) + XOR(payload + CRC16) + ``DC BA``;
the radio's replies carry no valid CRC (CHIRP ignores it too).
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass

from wokitoki.core.driver import DriverError
from wokitoki.core.transport import Transport, hexdump

log = logging.getLogger("wokitoki.driver.quansheng")

XOR_KEY = bytes([22, 108, 20, 230, 46, 145, 13, 64, 33, 53, 213, 64, 19, 3, 233, 128])
SESSION = b"\x6a\x39\x57\x64"  # the "timestamp" CHIRP sends with every command
BLOCK = 0x80  # largest block that reads/writes reliably

CMD_HELLO = 0x0514
REPLY_HELLO = 0x0515
REPLY_BOOTLOADER = 0x0518
CMD_READ = 0x051B
REPLY_READ = 0x051C
CMD_WRITE = 0x051D
REPLY_WRITE = 0x051E
CMD_RESET = 0x05DD


def xor(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data))


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def frame(payload: bytes) -> bytes:
    body = payload + struct.pack("<H", crc16_xmodem(payload))
    return struct.pack("<HH", 0xCDAB, len(payload)) + xor(body) + b"\xdc\xba"


def command(cmd: int, body: bytes) -> bytes:
    """Command id (u16 LE) + body length (u16 LE) + body."""
    return struct.pack("<HH", cmd, len(body)) + body


async def _sync(t: Transport, timeout: float, limit: int = 256) -> None:
    """Wait for the frame start AB CD. A radio that is just booting (after a
    reset) sends a few bytes of noise first – seen on our UV-K5: 98 DF 16 7F."""
    last = 0
    skipped = bytearray()
    while True:
        byte = (await t.read_exact(1, timeout))[0]
        if last == 0xAB and byte == 0xCD:
            if len(skipped) > 1:
                log.debug(
                    "skipped %d bytes before a frame: %s", len(skipped) - 1, hexdump(bytes(skipped[:-1]))
                )
            return
        skipped.append(byte)
        last = byte
        if len(skipped) > limit:
            raise DriverError(f"no frame start from the radio (got {hexdump(bytes(skipped[:16]))} …)")


MIN_REPLY = 6  # reply id (2) + length (2) + offset (2) – the write ACK is exactly this (VERIFIED)


async def exchange(t: Transport, payload: bytes, timeout: float = 2.0) -> bytes:
    """Send one command frame and return the de-obfuscated reply payload."""
    t.reset_input()
    await t.write(frame(payload))
    await _sync(t, timeout)
    size = await t.read_exact(2, timeout)
    length = size[0] | size[1] << 8
    if length < MIN_REPLY:
        raise DriverError(f"reply too short ({length} B) – not a UV-K5 answer")
    body = await t.read_exact(length + 4, timeout)  # payload + CRC (2) + footer (2)
    if body[-2:] != b"\xdc\xba":
        raise DriverError(f"bad reply footer {hexdump(body[-4:])}")
    return xor(body[:length])


@dataclass
class Hello:
    firmware: str


async def hello(t: Transport, timeout: float = 2.0) -> Hello:
    reply = await exchange(t, command(CMD_HELLO, SESSION), timeout)
    reply_id = reply[0] | reply[1] << 8
    if reply_id == REPLY_BOOTLOADER:
        raise DriverError("the radio is in bootloader (flashing) mode – restart it normally")
    if reply_id != REPLY_HELLO:
        raise DriverError(f"unexpected answer to hello: {hexdump(reply[:8])}")
    text = bytearray()
    for b in reply[4:20]:
        if not 0x20 <= b <= 0x7E:
            break
        text.append(b)
    firmware = text.decode("ascii")
    log.info("hello OK: firmware %s", firmware)
    return Hello(firmware=firmware)


async def read_block(t: Transport, offset: int, length: int = BLOCK) -> bytes:
    reply = await exchange(t, command(CMD_READ, struct.pack("<HBB", offset, length, 0) + SESSION))
    if reply[0] | reply[1] << 8 != REPLY_READ or (reply[4] | reply[5] << 8) != offset:
        raise DriverError(f"unexpected answer to read 0x{offset:04X}: {hexdump(reply[:8])}")
    data = reply[8 : 8 + length]
    if len(data) != length:
        raise DriverError(f"short read at 0x{offset:04X}: {len(data)}/{length} B")
    return data


async def write_block(t: Transport, offset: int, data: bytes) -> None:
    body = struct.pack("<HBB", offset, len(data), 1) + SESSION + data
    reply = await exchange(t, command(CMD_WRITE, body))
    if reply[0] | reply[1] << 8 != REPLY_WRITE or (reply[4] | reply[5] << 8) != offset:
        raise DriverError(f"the radio did not confirm the write at 0x{offset:04X}: {hexdump(reply[:8])}")


async def reset(t: Transport) -> None:
    """Reboot the radio so it loads the new EEPROM contents (no reply)."""
    await t.write(frame(command(CMD_RESET, b"")))


async def read_range(
    t: Transport, start: int, end: int, progress: Callable[[int, int], None] | None = None
) -> bytes:
    out = bytearray()
    total = (end - start) // BLOCK
    for n, offset in enumerate(range(start, end, BLOCK), 1):
        out += await read_block(t, offset)
        if progress:
            progress(n, total)
    return bytes(out)
