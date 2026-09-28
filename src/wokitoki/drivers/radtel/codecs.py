"""Byte-level codecs shared by Radtel drivers (RT-950 Pro, RT-900 family)."""

from __future__ import annotations

from dataclasses import dataclass, field

from wokitoki.core.codeplug import CodeplugError, Section

# Standard DCS codes incl. 645 (= CHIRP DTCS_CODES + 645), in radio table order.
DCS_BASE = (
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74, 114, 115, 116, 122, 125, 131,
    132, 134, 143, 145, 152, 155, 156, 162, 165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245,
    246, 251, 252, 255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325, 331, 332, 343, 346, 351,
    356, 364, 365, 371, 411, 412, 413, 423, 431, 432, 445, 446, 452, 454, 455, 462, 464, 465, 466,
    503, 506, 516, 523, 526, 532, 546, 565, 606, 612, 624, 627, 631, 632, 645, 654, 662, 664, 703,
    712, 723, 731, 732, 734, 743, 754,
)  # fmt: skip
# All codes Normal, then all codes Inverted (both radio families use this order).
DCS_CODES = tuple(f"D{c:03d}N" for c in DCS_BASE) + tuple(f"D{c:03d}I" for c in DCS_BASE)


def decode_bcd_frequency(b: bytes) -> int | None:
    """4 B packed BCD, least significant pair first, unit 10 Hz → Hz."""
    if all(x in (0x00, 0xFF) for x in b):
        return None
    value = 0
    for byte in reversed(b):
        high, low = byte >> 4, byte & 0x0F
        if high > 9 or low > 9:
            return None
        value = value * 100 + high * 10 + low
    return value * 10


def encode_bcd_frequency(hz: int) -> bytes:
    """Hz → 4 B packed BCD (unit 10 Hz, least significant pair first)."""
    if hz % 10:
        raise CodeplugError(f"{hz / 1e6:.6f} MHz: the radio stores frequencies in 10 Hz steps")
    value = hz // 10
    if not 0 < value <= 99_999_999:
        raise CodeplugError(f"{hz / 1e6} MHz is out of range")
    out = bytearray()
    for _ in range(4):
        pair = value % 100
        out.append((pair // 10) << 4 | pair % 10)
        value //= 100
    return bytes(out)


def decode_digit_frequency(b: bytes, hz_per_unit: int) -> int | None:
    """One decimal digit per byte, most significant first."""
    if all(x in (0x00, 0xFF) for x in b):
        return None
    value = 0
    for digit in b:
        if digit > 9:
            return None
        value = value * 10 + digit
    return value * hz_per_unit


def decode_text(b: bytes) -> str:
    """GB2312 text terminated by 0x00/0xFF."""
    end = next((i for i, x in enumerate(b) if x in (0x00, 0xFF)), len(b))
    return b[:end].decode("gb18030", errors="replace").strip()


def encode_text(text: str, length: int, *, what: str = "name") -> bytes:
    """Text → GB2312 bytes padded with 0xFF; too long or unsupported text is an error."""
    try:
        raw = text.encode("gb2312")
    except UnicodeEncodeError as e:
        raise CodeplugError(
            f"{what} {text!r}: character {text[e.start]!r} is not supported by the radio"
        ) from None
    if len(raw) > length:
        raise CodeplugError(f"{what} {text!r} is {len(raw)} bytes long, the radio allows {length}")
    return raw + b"\xff" * (length - len(raw))


def decode_ascii(b: bytes) -> str:
    end = next((i for i, x in enumerate(b) if x in (0x00, 0xFF)), len(b))
    return b[:end].decode("ascii", errors="replace").strip()


def u16(b: bytes, offset: int) -> int:
    return b[offset] | b[offset + 1] << 8


def i16(b: bytes, offset: int) -> int:
    v = u16(b, offset)
    return v - 0x10000 if v & 0x8000 else v


def pick(table: tuple, value: int):
    return table[value] if value < len(table) else value


@dataclass(frozen=True)
class Setting:
    key: str
    offset: int
    label: str  # written as a YAML comment
    kind: str = "int"  # int | bool | inverted_bool | choice | number | zone | map
    choices: tuple[str, ...] = ()
    mask: int = 0x0F
    shift: int = 0
    values: dict[int, str] = field(default_factory=dict)  # kind "map": raw byte → name
    # FF means "not set" (factory default) – except where every bit is a real
    # value, e.g. the F4HWN flags byte 0x1FF5 of the UV-K5.
    ff_unset: bool = True

    def unset(self, section: bytes) -> bool:
        return self.ff_unset and self.kind != "map" and section[self.offset] == 0xFF

    def decode(self, section: bytes):
        byte = section[self.offset]
        if self.kind == "map":
            return self.values.get(byte, byte)
        if self.unset(section):
            return None  # unset / factory default
        value = (byte >> self.shift) & self.mask
        if self.kind == "bool":
            return bool(value)
        if self.kind == "inverted_bool":
            return not value
        if self.kind == "choice":
            return self.choices[value] if value < len(self.choices) else value
        if self.kind == "zone":
            return value + 1
        return value

    def encode(self, section: bytearray, value) -> None:
        """Write ``value`` into ``section`` in place (only this setting's bits)."""
        if self.kind == "map":
            raise CodeplugError(f"{self.key} is read only")
        if value is None:
            raise CodeplugError(f"{self.key}: null (not set) cannot be written, give a value")
        if self.kind in ("bool", "inverted_bool"):
            if not isinstance(value, bool):
                raise CodeplugError(f"{self.key}: expected true or false, got {value!r}")
            raw = int(value != (self.kind == "inverted_bool"))
        elif self.kind == "choice":
            if value in self.choices:
                raw = self.choices.index(value)
            elif isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= self.mask:
                raw = value  # a raw number outside the known list (as decoded)
            else:
                raise CodeplugError(f"{self.key}: {value!r} is not one of {' | '.join(self.choices)}")
        elif self.kind == "zone":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= self.mask + 1:
                raise CodeplugError(f"{self.key}: expected a zone number, got {value!r}")
            raw = value - 1
        else:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= self.mask:
                raise CodeplugError(f"{self.key}: expected a number 0–{self.mask}, got {value!r}")
            raw = value
        byte = section[self.offset]
        # An unset (FF) byte starts from 0 – the caller makes sure every setting
        # sharing it gets a value; otherwise only this setting's bits change.
        base = 0 if self.unset(section) else byte & ~(self.mask << self.shift) & 0xFF
        section[self.offset] = base | (raw & self.mask) << self.shift

    def comment(self) -> str:
        if self.kind == "choice":
            return f"{self.label} ({' | '.join(self.choices)})"
        if self.kind == "map":
            return f"{self.label} ({' | '.join(self.values.values())})"
        return self.label


def decode_settings(table: tuple[Setting, ...], section: bytes) -> Section:
    out = Section()
    for setting in table:
        out.set(setting.key, setting.decode(section), setting.comment())
    return out
