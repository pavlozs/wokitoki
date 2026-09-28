"""RT-900 (BT) memory image layout and decoding into a Codeplug.

Taken from CHIRP ``chirp/drivers/radtel_rt900.py`` (class RT900BT, firmware
V1.20P); reading is verified on our radio over BLE and USB.
The image is the radio's linear address space 0x0000–0xF27F read in 64 B
blocks, exactly like CHIRP's memory map, so image offset == radio address.
See docs/radios/radtel-rt900.md.
"""

from __future__ import annotations

from wokitoki.core.codeplug import Channel, Codeplug, CodeplugError, Section, Tone, Zone, mhz, text

from .codecs import (
    DCS_BASE,
    Setting,
    decode_bcd_frequency,
    decode_digit_frequency,
    decode_settings,
    encode_bcd_frequency,
    pick,
    u16,
)
from .encoder import (
    ImageMap,
    changed,
    check_common,
    check_extra,
    extra_changed,
    fhss_value,
    patch_image,
    set_bit,
    set_bits,
)

CHANNEL_COUNT = 999  # FW V1.20P (older firmware: 512)
CHANNEL_SIZE = 32
VFO_OFFSET = 0x8000  # A, B × 32 B
SETTINGS_OFFSET = 0x9000
DTMF_OFFSET = 0xA000
RADIO_MODE_OFFSET = 0xD000
CALIBRATION = (0xF000, 0xF250)  # read only – NEVER write
# CHIRP reads range(0, 0xF250, 0x40): the last block ends at 0xF280.
IMAGE_SIZE = 0xF280

POWER_LEVELS = ("high", "mid", "low")  # CHIRP code: 0 = High, 1 = Mid, 2 = Low


def decode_tone(code: int) -> Tone:
    """RT-900: one little-endian u16. 0/FFFF off, ≥ 0x258 CTCSS in 0.1 Hz,
    1–0x69 DCS Normal (1-based index), 0x6A+ DCS Inverted (0-based)."""
    if code in (0, 0xFFFF):
        return "off"
    if code >= 0x258:
        return code / 10
    if 1 <= code <= 0x69:
        return f"D{DCS_BASE[code - 1]:03d}N"
    if code - 0x6A < len(DCS_BASE):
        return f"D{DCS_BASE[code - 0x6A]:03d}I"
    return f"?{code:04X}"


def decode_name(b: bytes) -> str:
    """12 characters; 0xFF can appear mid-name (CHIRP turns it into a space)."""
    raw = b.replace(b"\xff", b" ").replace(b"\x00", b"")
    return raw.decode("gb18030", errors="replace").rstrip()


def encode_tone(tone: Tone) -> int:
    if tone == "off":
        return 0
    if isinstance(tone, float | int):
        return round(tone * 10)
    code, polarity = int(tone[1:4]), tone[4]
    if code not in DCS_BASE:
        raise CodeplugError(f"DCS code {tone} is not supported by the radio")
    index = DCS_BASE.index(code)
    return index + 1 if polarity == "N" else index + 0x6A


def encode_name(name: str) -> bytes:
    try:
        raw = name.encode("ascii")
    except UnicodeEncodeError:
        raise CodeplugError(f"name {name!r}: the RT-900 supports only ASCII characters") from None
    if len(raw) > 12:
        raise CodeplugError(f"name {name!r} is {len(raw)} characters long, the radio allows 12")
    return raw + b"\xff" * (12 - len(raw))


EXTRA_KEYS = {
    "signal_group": (int, 15),
    "ptt_id": (int, 3),
    "scrambler": (int, 63),
    "encryption": (int, 3),
    "busy_lock": (bool, None),
    "learn_fhss": (bool, None),
    "fhss": (str, None),
}
# CHIRP set_memory starts a new channel from 16 × 00 + 16 × FF.
NEW_RECORD = b"\x00" * 16 + b"\xff" * 16


def encode_channel(r: bytearray, old: Channel | None, new: Channel) -> None:
    """Mirror of decode_channel; patches only fields that differ from ``old``."""
    check_common(old, new, POWER_LEVELS)
    check_extra(new, EXTRA_KEYS)
    if changed(old, new, "rx"):
        r[0:4] = encode_bcd_frequency(new.rx)
    if changed(old, new, "tx"):
        r[4:8] = b"\xff\xff\xff\xff" if new.tx is None else encode_bcd_frequency(new.tx)
    if changed(old, new, "rx_tone"):
        r[8:10] = encode_tone(new.rx_tone).to_bytes(2, "little")
    if changed(old, new, "tx_tone"):
        r[10:12] = encode_tone(new.tx_tone).to_bytes(2, "little")
    if extra_changed(old, new, "signal_group", 0):
        set_bits(r, 12, 0x0F, 0, new.extra.get("signal_group", 0))
    if extra_changed(old, new, "ptt_id", 0):
        set_bits(r, 13, 0x03, 0, new.extra.get("ptt_id", 0))
    if extra_changed(old, new, "scrambler", 0):
        set_bits(r, 14, 0x3F, 2, new.extra.get("scrambler", 0))
    if changed(old, new, "power"):
        set_bits(r, 14, 0x03, 0, POWER_LEVELS.index(new.power))
    if changed(old, new, "bw"):
        set_bit(r, 15, 0x40, new.bw == "narrow")
    if extra_changed(old, new, "encryption", 0):
        set_bits(r, 15, 0x03, 4, new.extra.get("encryption", 0))
    if extra_changed(old, new, "busy_lock", False):
        set_bit(r, 15, 0x08, new.extra.get("busy_lock", False))
    if changed(old, new, "scan"):
        set_bit(r, 15, 0x04, new.scan)
    if changed(old, new, "mode"):
        set_bit(r, 15, 0x02, new.mode == "AM")
    if extra_changed(old, new, "learn_fhss", False):
        set_bit(r, 15, 0x01, new.extra.get("learn_fhss", False))
    if extra_changed(old, new, "fhss", None):
        value = fhss_value(new.extra.get("fhss"))
        if value is not None and value > 0x7FFFFF:
            raise CodeplugError("extra.fhss must be 000000–7FFFFF on the RT-900")
        # Like the OEM software: no code = FFFFFF + flag FF, a code = value + flag A0.
        r[16:20] = b"\xff" * 4 if value is None else value.to_bytes(3, "little") + b"\xa0"
    if changed(old, new, "name"):
        r[20:32] = encode_name(new.name)


def decode_channel(b: bytes, slot: int) -> Channel | None:
    if b[0] == 0xFF:
        return None
    rx = decode_bcd_frequency(b[0:4])
    if not rx:
        return None
    tx = None if b[4:8] == b"\xff\xff\xff\xff" else decode_bcd_frequency(b[4:8])
    flags = b[15]
    extra: dict = {}
    if b[12] & 0x0F:
        extra["signal_group"] = b[12] & 0x0F
    if b[13] & 0x03:
        extra["ptt_id"] = b[13] & 0x03
    if b[14] >> 2:
        extra["scrambler"] = b[14] >> 2
    if (flags >> 4) & 0x03:
        extra["encryption"] = (flags >> 4) & 0x03
    if flags & 0x08:
        extra["busy_lock"] = True
    if flags & 0x01:
        extra["learn_fhss"] = True
    code = b[16] | b[17] << 8 | b[18] << 16
    if code != 0xFFFFFF:
        extra["fhss"] = f"{code & 0x7FFFFF:06X}"
    power = b[14] & 0x03
    return Channel(
        slot=slot,
        rx=rx,
        tx=tx,
        name=decode_name(b[20:32]),
        mode="AM" if flags & 0x02 else "FM",
        bw="narrow" if flags & 0x40 else "wide",
        power=POWER_LEVELS[power] if power < 3 else f"?{power}",
        rx_tone=decode_tone(u16(b, 8)),
        tx_tone=decode_tone(u16(b, 10)),
        scan=bool(flags & 0x04),
        extra=extra,
    )


def decode_channels(image: bytes) -> Zone:
    """The RT-900 has no zones – all memories form one list."""
    zone = Zone(number=1, size=CHANNEL_COUNT, first_slot=1)
    for i in range(CHANNEL_COUNT):
        start = i * CHANNEL_SIZE
        channel = decode_channel(image[start : start + CHANNEL_SIZE], i + 1)
        if channel is not None:
            zone.channels.append(channel)
    return zone


# --- VFO ----------------------------------------------------------------------------

SHIFTS = ("simplex", "+", "-")
# CHIRP _step_map: index → kHz (8.33 was added last, at index 8)
STEPS_KHZ = (2.5, 5, 6.25, 10, 12.5, 20, 25, 50, 8.33)


def decode_vfo(b: bytes) -> Section:
    s = Section()
    s.set("rx", mhz(decode_digit_frequency(b[0:8], 10)))
    s.set("shift", pick(SHIFTS, (b[14] >> 4) & 0x03))
    s.set("offset", mhz(decode_digit_frequency(b[20:26], 1000)))
    s.set("rx_tone", decode_tone(u16(b, 8)))
    s.set("tx_tone", decode_tone(u16(b, 10)))
    s.set("mode", "AM" if b[17] & 0x02 else "FM")
    s.set("bw", "narrow" if b[17] & 0x40 else "wide")
    power = b[16] & 0x03
    s.set("power", POWER_LEVELS[power] if power < 3 else power)
    s.set("step", pick(STEPS_KHZ, b[19]))
    extra = {}
    if b[14] & 0x0F:
        extra["signal_group"] = b[14] & 0x0F
    if (b[16] >> 2) & 0x07:
        extra["scrambler"] = (b[16] >> 2) & 0x07
    if (b[17] >> 4) & 0x03:
        extra["encryption"] = (b[17] >> 4) & 0x03
    if extra:
        s.set("extra", extra)
    return s


# --- settings (0x9000) --------------------------------------------------------------

SKEYS = ("radio", "tx-power", "scan", "search", "noaa", "sos", "am-fm", "bluetooth")
DELAYS_MS = ("off", *(f"{x}ms" for x in range(100, 1100, 100)))

SETTINGS = (
    Setting("sql", 0x00, "squelch 0–9"),
    Setting("save_mode", 0x01, "battery save", "choice", ("off", "normal", "super", "deep"), mask=0x03),
    Setting("vox", 0x02, "VOX 0 = off, 1–9"),
    Setting("auto_backlight", 0x03, "backlight", "choice",
            ("on", "5s", "10s", "15s", "20s", "30s", "1min", "2min", "3min")),
    Setting("tdr", 0x04, "dual watch (TDR)", "bool", mask=0x01),
    Setting("tot", 0x05, "transmit time-out", "choice", ("off", "15s", "30s", "45s", "60s", "75s"),
            mask=0x07),
    Setting("beep_prompt", 0x06, "key beep", "bool", mask=0x01),
    Setting("voice_prompt", 0x07, "voice prompts", "bool", mask=0x01),
    Setting("language", 0x08, "language", "choice", ("english", "chinese"), mask=0x01),
    Setting("dtmf_side_tone", 0x09, "DTMF side tone", "choice", ("off", "dt-st", "ani-st", "dt+ani"), mask=0x03),
    Setting("scan_mode", 0x0A, "scan mode", "choice", ("time", "carrier", "search"), mask=0x03),
    Setting("ptt_id", 0x0B, "PTT ID", "choice", ("off", "bot", "eot", "both")),
    Setting("ptt_delay", 0x0C, "PTT delay", "choice",
            ("none", "100ms", "200ms", "400ms", "600ms", "800ms", "1000ms"), mask=0x07),
    Setting("display_mode_a", 0x0D, "display A", "choice", ("name", "frequency", "channel"), mask=0x03),
    Setting("display_mode_b", 0x0E, "display B", "choice", ("name", "frequency", "channel"), mask=0x03),
    Setting("busy_lock", 0x0F, "busy channel lockout", "bool", mask=0xFF),
    Setting("auto_key_lock", 0x10, "auto key lock", "choice", ("off", "5s", "10s", "15s")),
    Setting("alarm_mode", 0x11, "alarm mode", "choice", ("on-site", "send-sound", "send-code"), mask=0x03),
    Setting("alarm_sound", 0x12, "alarm sound", "bool", mask=0x01),
    Setting("dual_tx", 0x13, "dual TX", "choice", ("off", "A", "B"), mask=0x03),
    Setting("tail_noise_clear", 0x14, "tail noise clear", "bool", mask=0x01),
    Setting("repeater_noise_clear", 0x15, "repeater noise clear", "choice", DELAYS_MS),
    Setting("repeater_noise_delay", 0x16, "repeater noise delay", "choice", DELAYS_MS),
    Setting("roger", 0x17, "roger beep", "choice", ("off", "beep", "tone1200"), mask=0x03),
    Setting("tx_ab", 0x18, "TX A/B", "number", mask=0xFF),
    Setting("fm_radio", 0x19, "FM radio enabled", "inverted_bool", mask=0x01),
    Setting("work_mode_a", 0x1A, "work mode A", "choice", ("vfo", "channel"), mask=0x01, shift=0),
    Setting("work_mode_b", 0x1A, "work mode B", "choice", ("vfo", "channel"), mask=0x01, shift=4),
    Setting("lock_keyboard", 0x1B, "keypad lock", "bool", mask=0x01),
    Setting("power_on_message", 0x1C, "power-on screen", "choice", ("logo", "voltage"), mask=0x01),
    Setting("bluetooth", 0x1D, "Bluetooth", "bool", mask=0xFF),
    Setting("pilot_tone", 0x1E, "repeater pilot tone", "choice", ("1000Hz", "1450Hz", "1750Hz", "2100Hz"), mask=0x03),
    Setting("vox_delay", 0x20, "VOX delay", "choice", tuple(f"{x / 10}s" for x in range(5, 21))),
    Setting("menu_quit", 0x21, "menu auto-exit", "choice",
            (*(f"{x}s" for x in range(5, 55, 5)), "60s")),
    Setting("single_mode", 0x24, "single mode", "bool", mask=0xFF),
    Setting("qt_save", 0x29, "QT save", "choice", ("all", "tx", "rx"), mask=0x03),
    # _has_sp0: the side-key addresses are shifted by one on the RT-900 BT
    Setting("side_key2_short", 0x2A, "side key 2 short", "choice", SKEYS, mask=0xFF),
    Setting("side_key2_long", 0x2B, "side key 2 long", "choice", SKEYS, mask=0xFF),
    Setting("side_key3_short", 0x2C, "side key 3 short", "choice", SKEYS, mask=0xFF),
    Setting("noise_reduction", 0x31, "noise reduction (BT version)", "bool", mask=0xFF),
    Setting("fm_interrupt", 0x34, "FM radio interrupted by reception", "bool", mask=0xFF),
)  # fmt: skip

RADIO_MODES = {
    0xFF: "default",
    0xA5: "gmrs",
    0x66: "pmr",
    0x55: "144-146/430-440",
    0x56: "super",
    0x00: "factory",
    0x28: "unknown-2",
}

# --- DTMF (0xA000) ------------------------------------------------------------------

DTMF_CHARS = "0123456789 *#ABCD"  # CHIRP DTMF_CHARS (differs from the RT-950 Pro)
DTMF_SPEEDS = ("50ms", "100ms", "200ms", "300ms", "500ms")


def decode_dtmf_code(b: bytes) -> str:
    out = []
    for x in b:
        if x == 0xFF:
            break
        if x < len(DTMF_CHARS):
            out.append(DTMF_CHARS[x])
    return "".join(out).strip()


def decode_dtmf(b: bytes) -> Section:
    s = decode_settings(
        (
            Setting("speed_on", 0x07, "tone length", "choice", DTMF_SPEEDS, mask=0x07),
            Setting("speed_off", 0x08, "gap between tones", "choice", DTMF_SPEEDS, mask=0x07),
        ),
        b,
    )
    groups = []
    for n in range(15):
        code = decode_dtmf_code(b[0x20 + n * 16 : 0x20 + n * 16 + 6])
        if code:
            groups.append({"n": n + 1, "code": text(code)})
    s.set("groups", groups, "PTT ID codes 1–15, max 6 characters")
    return s


# --- whole image ----------------------------------------------------------------------


def decode_image(image: bytes, driver_id: str) -> Codeplug:
    cp = Codeplug(driver=driver_id)
    cp.zones = [decode_channels(image)]
    cp.vfo = {
        name: decode_vfo(image[VFO_OFFSET + i * 32 : VFO_OFFSET + (i + 1) * 32])
        for i, name in enumerate("AB")
    }
    cp.settings = decode_settings(SETTINGS, image[SETTINGS_OFFSET : SETTINGS_OFFSET + 0x40])
    mode = Setting("radio_mode", 0, "hidden radio mode – read only", "map", values=RADIO_MODES)
    cp.settings.set(
        "radio_mode", mode.decode(image[RADIO_MODE_OFFSET : RADIO_MODE_OFFSET + 1]), mode.comment()
    )
    cp.extra["dtmf"] = decode_dtmf(image[DTMF_OFFSET : DTMF_OFFSET + 0x140])
    cp.notes = [
        "RT-900: 999 channels without zones – zone 1 = the whole memory.",
        "Memory map taken from CHIRP (RT-900 BT, FW V1.20P); reading verified, some field meanings not yet.",
        "Omitted rx_tone/tx_tone = off, omitted extra = defaults. null = not set in the radio (FF).",
        "Unknown bytes and calibration live only in the .img – it is the base for writing.",
    ]
    return cp


IMAGE_MAP = ImageMap(
    decode=lambda image: decode_image(image, "radtel-rt900"),
    encode_channel=encode_channel,
    new_record=NEW_RECORD,
    settings=SETTINGS,
    settings_offset=SETTINGS_OFFSET,
    settings_length=0x40,
)


def encode_image(cp: Codeplug, base: bytes) -> bytes:
    return patch_image(base, cp, IMAGE_MAP)
