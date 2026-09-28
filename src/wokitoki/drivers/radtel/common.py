"""Shared Radtel clone protocol: handshake, XOR key negotiation, block reads, END.

Ported from the RT950Pro macOS app (BLE/RT950Protocol.swift, BLEManager.swift),
verified there on a real RT-950 Pro. The RT-900 family uses the same scheme
with a fixed SEND frame and no "M" command (CHIRP radtel_rt900.py / mml_jc8810.py).
See docs/radios/radtel-*.md.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, replace

from wokitoki.core.driver import DriverError
from wokitoki.core.transport import Transport, TransportError, TransportTimeout, hexdump

log = logging.getLogger("wokitoki.driver.radtel")

ACK = 0x06
END = b"E"

# Candidate 4-byte XOR keys; the "SEND" frame selects one of them.
ENCRYPT_STRINGS: tuple[bytes, ...] = tuple(
    s.encode("ascii")
    for s in (
        "BHT ", "CO 7", "A ES", " EIY", "M PQ", "XN Y", "RVB ", " HQP", "W RC", "MS N",
        " SAT", "K DH", "ZO R", "C SL", "6RB ", " JCG", "PN V", "J PK", "EK L", "I LZ",
    )
)  # fmt: skip


def build_encryption_frame(rng: random.Random | None = None) -> tuple[bytes, bytes]:
    """Return the 25-byte ``SEND`` frame and the XOR key it selects.

    The key is derived from the frame's own random bytes, so no secret is
    exchanged – both sides just compute the same index.
    """
    rng = rng or random.Random()
    code = (rng.randint(1, 2) << 4) | rng.randint(0, 4)
    # "SEND", code, 19 random symbol indexes, trailing zero (25 B as in the verified app).
    frame = b"SEND" + bytes([code]) + bytes(rng.randint(0, 19) for _ in range(19)) + b"\x00"
    return frame, key_for_frame(frame)


def key_for_frame(frame: bytes) -> bytes:
    """XOR key selected by a SEND frame (the radio computes the same)."""
    code = frame[4]
    idx = (code - 0x20) * 2 + 1 if code & 0x20 else (code - 0x10) * 2
    return ENCRYPT_STRINGS[frame[5 + idx] % len(ENCRYPT_STRINGS)]


def xor_crypt(payload: bytes, key: bytes) -> bytes:
    """Symmetric keystream used for block payloads.

    Bytes 0x00/0xFF, bytes equal to the key byte or its complement, and
    positions where the key byte is a space are left untouched.
    """
    out = bytearray(payload)
    for i, value in enumerate(out):
        k = key[i % len(key)]
        if k != 0x20 and value not in (0x00, 0xFF, k, k ^ 0xFF):
            out[i] = value ^ k
    return bytes(out)


@dataclass
class CloneSession:
    ident: bytes
    model: str
    key: bytes


@dataclass(frozen=True)
class HandshakeSpec:
    magic: bytes
    ident_length: int = 16
    model_length: int | None = 12  # None: the radio has no "M" command
    send_frame: bytes | None = None  # fixed SEND frame; None = random (RT-950 Pro)
    known_idents: tuple[bytes, ...] = ()  # first 8 B of the ident; others only warn
    timeout: float = 2.5
    attempts: int = 3


def quick_spec(spec: HandshakeSpec) -> HandshakeSpec:
    """One short attempt – for probing a port where silence means "not this radio"."""
    return replace(spec, attempts=1, timeout=1.0)


async def _expect_ack(t: Transport, what: str, timeout: float) -> None:
    reply = await t.read_exact(1, timeout)
    if reply[0] != ACK:
        raise DriverError(f"{what}: expected ACK 06, got {hexdump(reply)}")


async def handshake_once(t: Transport, spec: HandshakeSpec, rng: random.Random | None = None) -> CloneSession:
    await t.write(spec.magic)
    await _expect_ack(t, "handshake", spec.timeout)

    await t.write(b"F")
    ident = await t.read_exact(spec.ident_length, spec.timeout)
    # A late duplicate ACK can shift the ident block by one byte.
    for _ in range(4):
        if ident[0] != ACK:
            break
        ident = ident[1:] + await t.read_exact(1, spec.timeout)

    if spec.known_idents and not any(ident.startswith(k) for k in spec.known_idents):
        log.warning("unknown radio ident %s – another model or firmware?", hexdump(ident))

    model = ""
    if spec.model_length:
        await t.write(b"M")
        raw = await t.read_exact(spec.model_length, spec.timeout)
        model = raw.decode("ascii", errors="replace").strip("\x00 \xff")
        if not model:
            raise DriverError(f"the radio returned an empty model id ({hexdump(raw)})")

    if spec.send_frame:
        frame, key = spec.send_frame, key_for_frame(spec.send_frame)
    else:
        frame, key = build_encryption_frame(rng)
    await t.write(frame)
    await _expect_ack(t, "encryption setup", spec.timeout)
    log.info("handshake OK: model %s, ident %s", model or "?", hexdump(ident))
    return CloneSession(ident=ident, model=model, key=key)


async def open_session(t: Transport, spec: HandshakeSpec, rng: random.Random | None = None) -> CloneSession:
    """Handshake with retries: the radio sometimes swallows the first round trip."""
    last: Exception | None = None
    for attempt in range(1, spec.attempts + 1):
        await asyncio.sleep(0.2)
        t.reset_input()
        try:
            return await handshake_once(t, spec, rng)
        except (DriverError, TransportError) as e:
            last = e
            # A single quick attempt is a probe ("is it this radio?") – failing is normal.
            level = logging.WARNING if spec.attempts > 1 else logging.DEBUG
            log.log(level, "handshake attempt %d/%d failed: %s", attempt, spec.attempts, e)
    raise DriverError(f"handshake failed after {spec.attempts} attempts: {last}")


async def close_session(t: Transport) -> None:
    """Leave clone mode so the radio goes back to normal operation."""
    await t.write(END)
    await asyncio.sleep(0.3)
    t.reset_input()


@dataclass(frozen=True)
class Segment:
    """A contiguous region read with one command byte (write command for later)."""

    read_cmd: int
    write_cmd: int
    start: int
    length: int
    name: str
    block_size: int = 0x80
    plain_from: int | None = None  # blocks at/after this address are not encrypted

    @property
    def blocks(self) -> int:
        return -(-self.length // self.block_size)

    def encrypted(self, address: int) -> bool:
        return self.plain_from is None or address < self.plain_from


async def read_block(
    t: Transport, cmd: int, address: int, size: int, key: bytes | None, *, timeout: float = 3.0
) -> bytes:
    """Read one block (``[cmd, hi, lo, size]`` → 4 B header + payload) and decrypt it
    (``key=None``: the block is sent in plain text).

    Retries up to 3× when nothing at all arrived (BLE occasionally drops a burst)
    or the reply belongs to another address (a late burst) – such data is never
    stored, it would end up at the wrong place of the image and later be written.
    """
    header = bytes([cmd, address >> 8, address & 0xFF, size])
    for attempt in range(1, 4):
        t.reset_input()
        await t.write(header)
        try:
            reply = await t.read_exact(4 + size, timeout)
        except TransportTimeout as e:
            if e.received or attempt == 3:
                raise
            log.warning("block 0x%04X got no answer, retrying (%d/3)", address, attempt)
            await asyncio.sleep(0.1)
            continue
        if reply[:3] != header[:3]:
            if attempt == 3:
                raise DriverError(
                    f"block 0x{address:04X}: reply header {hexdump(reply[:4])} does not match the request"
                )
            log.warning(
                "reply header %s does not match request %s, retrying", hexdump(reply[:4]), hexdump(header)
            )
            await asyncio.sleep(0.2)  # let a late burst arrive; reset_input() drops it
            continue
        return xor_crypt(reply[4:], key) if key else reply[4:]
    raise AssertionError("unreachable")


async def read_segments(
    t: Transport,
    spec: HandshakeSpec,
    segments: tuple[Segment, ...],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[bytes, CloneSession]:
    """Open a clone session right before the transfer, read all segments, send END.

    The image is the plain concatenation of the segments (same layout as the
    RT950Pro app's .img backups).
    """
    session = await open_session(t, spec)
    total = sum(s.blocks for s in segments)
    done = 0
    image = bytearray()
    for segment in segments:
        log.info(
            "reading %s: command 0x%02X, 0x%04X + 0x%04X",
            segment.name,
            segment.read_cmd,
            segment.start,
            segment.length,
        )
        for address in range(segment.start, segment.start + segment.length, segment.block_size):
            try:
                key = session.key if segment.encrypted(address) else None
                block = await read_block(t, segment.read_cmd, address, segment.block_size, key)
            except TransportTimeout as e:
                if done or e.received:
                    raise
                # The radio sometimes ignores the first block – reopen the session once.
                log.warning("no answer to the first block, reopening the clone session")
                session = await open_session(t, spec)
                key = session.key if segment.encrypted(address) else None
                block = await read_block(t, segment.read_cmd, address, segment.block_size, key)
            image += block
            done += 1
            if progress:
                progress(done, total)
    await close_session(t)
    return bytes(image), session


@dataclass(frozen=True)
class WriteArea:
    """A region the driver may write, in radio addresses and image offsets."""

    write_cmd: int
    start: int
    length: int
    image_offset: int
    name: str
    block_size: int = 0x80
    ack_timeout: float = 8.0
    post_delay: float = 0.0  # pause after each block (settings/APRS flash)
    commit: bool = False  # RT-950 Pro APRS block: the radio saves to flash and drops BLE
    plain_from: int | None = None

    @property
    def blocks(self) -> int:
        return -(-self.length // self.block_size)

    def image_range(self) -> tuple[int, int]:
        return self.image_offset, self.image_offset + self.blocks * self.block_size


async def _wait_ack(t: Transport, address: int, timeout: float) -> None:
    """The radio answers a stored block with 06; a header echo may precede it."""
    got = await t.read_exact(1, timeout)
    while got[-1] != ACK:
        if len(got) >= 4:
            raise DriverError(f"the radio rejected block 0x{address:04X} (answer {hexdump(got)})")
        try:
            got += await t.read_exact(1, 0.5)
        except TransportTimeout:
            raise DriverError(f"the radio rejected block 0x{address:04X} (answer {hexdump(got)})") from None


async def write_areas(
    t: Transport,
    spec: HandshakeSpec,
    areas: tuple[WriteArea, ...],
    image: bytes,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Open a clone session and write all areas block by block (each block is ACKed)."""
    session = await open_session(t, spec)
    total = sum(a.blocks for a in areas)
    done = 0
    for area in areas:
        log.info(
            "writing %s: command 0x%02X, 0x%04X + 0x%04X", area.name, area.write_cmd, area.start, area.length
        )
        for n in range(area.blocks):
            address = area.start + n * area.block_size
            offset = area.image_offset + n * area.block_size
            payload = image[offset : offset + area.block_size]
            if area.plain_from is None or address < area.plain_from:
                payload = xor_crypt(payload, session.key)
            t.reset_input()
            await t.write(
                bytes([area.write_cmd, address >> 8, address & 0xFF, area.block_size]) + payload,
                confirm=area.commit,
            )
            await _wait_ack(t, address, area.ack_timeout)
            done += 1
            if progress:
                progress(done, total)
            if area.post_delay:
                await asyncio.sleep(area.post_delay)
        if area.commit:
            await asyncio.sleep(0.5)
    # END is best effort: every block was already confirmed, and after a
    # commit block the radio usually drops the connection by itself.
    try:
        await t.write(END)
        await asyncio.sleep(0.5)
    except TransportError as e:
        log.info("END after writing not delivered (%s) – expected after a commit", e)
