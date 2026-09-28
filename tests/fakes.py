"""A scripted Radtel radio behind the Transport interface (no hardware needed)."""

from __future__ import annotations

import struct

from wokitoki.core.transport import BufferedTransport
from wokitoki.drivers.radtel import rt900, rt950pro
from wokitoki.drivers.radtel.common import ENCRYPT_STRINGS, xor_crypt

WRITE_TO_READ = {0x57: 0x52, 0x58: 0x54}  # write command → read command of the same memory
RT950_IDENT = bytes.fromhex("01360174040005200200026001033004")
RT900_IDENT = bytes.fromhex("01360180040005200200026001033004")


class FakeRadtel(BufferedTransport):
    kind = "fake"

    def __init__(
        self,
        *,
        magic: bytes = b"PROGRAMBT9000U",
        ident: bytes = RT950_IDENT,
        model: bytes | None = b"RT-950\x00\x00\x00\x00\x00\x00",  # None: no "M" command
        ignore_first: int = 0,
        duplicate_ack: bool = False,
        image: bytes | None = None,
        segments=rt950pro.SEGMENTS,
    ):
        super().__init__()
        self.magic = magic
        self.ident = ident
        self.model = model
        self.ignore_first = ignore_first  # swallow the first N magic writes
        self.duplicate_ack = duplicate_ack
        self.written: list[bytes] = []
        self.written_blocks: list[tuple[int, int]] = []
        self.segments = segments
        self.key: bytes | None = None
        # (read command, radio address) → (block, encrypted)
        self.memory: dict[tuple[int, int], tuple[bytes, bool]] = {}
        if image is not None:
            offset = 0
            for seg in segments:
                for addr in range(seg.start, seg.start + seg.length, seg.block_size):
                    block = image[offset : offset + seg.block_size]
                    self.memory[(seg.read_cmd, addr)] = (block, seg.encrypted(addr))
                    offset += seg.block_size
        self.opened = self.closed = False

    @property
    def description(self) -> str:
        return "fake radio"

    async def open(self) -> None:
        self.opened = True
        self._connected = True

    async def close(self) -> None:
        self.closed = True
        self._connected = False

    async def write(self, data: bytes, *, confirm: bool = False) -> None:
        self.written.append(bytes(data))
        if data == self.magic:
            if self.ignore_first:
                self.ignore_first -= 1
                return
            self._feed(b"\x06\x06" if self.duplicate_ack else b"\x06")
        elif data == b"F":
            self._feed(self.ident)
        elif data == b"M" and self.model is not None:
            self._feed(self.model)
        elif data.startswith(b"SEND") and len(data) == 25:
            self.key = key_from_frame(data)
            self._feed(b"\x06")
        elif len(data) > 4 and data[0] in WRITE_TO_READ and len(data) == 4 + data[3]:
            key = (WRITE_TO_READ[data[0]], (data[1] << 8) | data[2])
            self.written_blocks.append(key)
            if key not in self.memory:
                self._feed(b"\x15")  # NAK: the fake has no such block
                return
            _old, encrypted = self.memory[key]
            payload = bytes(data[4:])
            self.memory[key] = (xor_crypt(payload, self.key) if encrypted else payload, encrypted)
            self._feed(b"\x06")
        elif len(data) == 4 and (data[0], (data[1] << 8) | data[2]) in self.memory:
            block, encrypted = self.memory[(data[0], (data[1] << 8) | data[2])]
            self._feed(data + (xor_crypt(block, self.key) if encrypted else block))


def key_from_frame(frame: bytes) -> bytes:
    """What the radio does with the SEND frame (independent of the driver code)."""
    code = frame[4]
    idx = (code - 0x20) * 2 + 1 if code & 0x20 else (code - 0x10) * 2
    return ENCRYPT_STRINGS[frame[5 + idx] % len(ENCRYPT_STRINGS)]


def fake_rt900(**kwargs) -> FakeRadtel:
    return FakeRadtel(magic=b"PROGRAMBT80U", ident=RT900_IDENT, model=None, segments=rt900.SEGMENTS, **kwargs)


def fake_image(radio: FakeRadtel) -> bytes:
    """The fake's memory in .img layout (to compare with what was written)."""
    out = bytearray()
    for seg in radio.segments:
        for addr in range(seg.start, seg.start + seg.length, seg.block_size):
            out += radio.memory[(seg.read_cmd, addr)][0]
    return bytes(out)


class FakeUVK5(BufferedTransport):
    """A UV-K5 speaking the Quansheng frame protocol, with an 8 KB EEPROM."""

    kind = "fake"

    def __init__(self, image: bytes, firmware: str = "F4HWN v4.2"):
        super().__init__()
        self.eeprom = bytearray(image)
        self.firmware = firmware
        self.writes: list[int] = []  # offsets of written blocks
        self.resets = 0
        self._pending = bytearray()

    @property
    def description(self) -> str:
        return "fake UV-K5"

    async def open(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    def _reply(self, payload: bytes) -> None:
        from wokitoki.drivers.quansheng import protocol as p

        self._feed(struct.pack("<HH", 0xCDAB, len(payload)) + p.xor(payload + b"\xff\xff") + b"\xdc\xba")

    async def write(self, data: bytes, *, confirm: bool = False) -> None:
        from wokitoki.drivers.quansheng import protocol as p

        self._pending += data
        while len(self._pending) >= 8 and self._pending[:2] == b"\xab\xcd":
            length = self._pending[2] | self._pending[3] << 8
            if len(self._pending) < length + 8:
                return
            payload = p.xor(bytes(self._pending[4 : 4 + length + 2]))[:length]
            del self._pending[: length + 8]
            cmd = payload[0] | payload[1] << 8
            if cmd == p.CMD_HELLO:
                self._reply(
                    struct.pack("<HH", p.REPLY_HELLO, 20)
                    + self.firmware.encode().ljust(16, b"\x00")
                    + b"\x00" * 4
                )
            elif cmd == p.CMD_READ:
                offset, n = payload[4] | payload[5] << 8, payload[6]
                self._reply(
                    struct.pack("<HHHBB", p.REPLY_READ, 4 + n, offset, n, 0)
                    + bytes(self.eeprom[offset : offset + n])
                )
            elif cmd == p.CMD_WRITE:
                offset, n = payload[4] | payload[5] << 8, payload[6]
                self.eeprom[offset : offset + n] = payload[12 : 12 + n]
                self.writes.append(offset)
                self._reply(struct.pack("<HHH", p.REPLY_WRITE, 2, offset))  # 6 B, like the real radio
            elif cmd == p.CMD_RESET:
                self.resets += 1
        if self._pending and self._pending[:2] != b"\xab\xcd":
            self._pending.clear()  # not our protocol (e.g. a Radtel magic while probing)
