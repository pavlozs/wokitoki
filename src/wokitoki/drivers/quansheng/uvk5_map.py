"""UV-K5 EEPROM layout for the F4HWN firmware v4.x and decoding/encoding.

Taken from the F4HWN CHIRP driver (armel/uv-k5-chirp-driver,
uvk5_egzumer_f4hwn_ver_4_2_0.py, MEM_FORMAT). The image is the whole 8 KB
EEPROM, image offset == EEPROM address. See docs/radios/quansheng-uvk5-f4hwn.md.
"""

from __future__ import annotations

from wokitoki.core.codeplug import Channel, Codeplug, CodeplugError, Section, Tone, Zone, mhz, plain, text
from wokitoki.drivers.radtel.codecs import Setting, decode_settings
from wokitoki.drivers.radtel.encoder import (
    ImageMap,
    changed,
    check_common,
    check_extra,
    extra_changed,
    fm_entries,
    patch_image,
    set_bit,
    set_bits,
)

IMAGE_SIZE = 0x2000
CHANNEL_COUNT = 200
VFO_SLOTS = 14  # channel[200..213]: VFO A/B for 7 bands
CHANNEL_SIZE = 16
ATTR_OFFSET = 0x0D60  # 1 B per channel (+ 7 for the VFO bands)
FM_OFFSET = 0x0E40  # 20 × u16, 100 kHz units
NAME_OFFSET = 0x0F50  # 16 B per channel, 10 characters used
BUILD_OPTIONS = 0x1FF0  # firmware feature flags (read only)
CALIBRATION = 0x1E00  # never written
# What the CHIRP driver writes: 0x0000–0x1CFF and the F4HWN settings 0x1FF2–0x1FFF.
WRITABLE = ((0x0000, 0x1D00), (0x1FF2, 0x2000))

# txpower field (3 bits) → level; names follow the F4HWN menu.
POWER_LEVELS = ("user", "low1", "low2", "low3", "low4", "low5", "mid", "high")
POWER_WATTS = (
    "user = SetPwr menu, low1 <20 mW, low2 125 mW, low3 250 mW, low4 500 mW, low5 1 W, mid 2 W, high 5 W"
)
MODES = ("FM", "AM", "USB")
STEPS_KHZ = (2.5, 5, 6.25, 10, 12.5, 25, 8.33, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 1.25, 9, 15, 20, 30, 50, 100, 125, 200,
             250, 500)  # fmt: skip
DEFAULT_STEP = 12.5
CTCSS = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9,
    114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1,
    225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
)  # fmt: skip
DCS = (
    23,
    25,
    26,
    31,
    32,
    36,
    43,
    47,
    51,
    53,
    54,
    65,
    71,
    72,
    73,
    74,
    114,
    115,
    116,
    122,
    125,
    131,
    132,
    134,
    143,
    145,
    152,
    155,
    156,
    162,
    165,
    172,
    174,
    205,
    212,
    223,
    225,
    226,
    243,
    244,
    245,
    246,
    251,
    252,
    255,
    261,
    263,
    265,
    266,
    271,
    274,
    306,
    311,
    315,
    325,
    331,
    332,
    343,
    346,
    351,
    356,
    364,
    365,
    371,
    411,
    412,
    413,
    423,
    431,
    432,
    445,
    446,
    452,
    454,
    455,
    462,
    464,
    465,
    466,
    503,
    506,
    516,
    523,
    526,
    532,
    546,
    565,
    606,
    612,
    624,
    627,
    631,
    632,
    654,
    662,
    664,
    703,
    712,
    723,
    731,
    732,
    734,
    743,
    754,
)  # fmt: skip (no 645 on the K5)
# Band of a channel (stored in its attribute byte); first match wins, like CHIRP.
BANDS_STANDARD = ((50, 76), (108, 136.9999), (137, 173.9999), (174, 349.9999), (350, 399.9999), (400, 469.9999),
                  (470, 600))  # fmt: skip
BANDS_WIDE = ((18, 108), (108, 136.9999), (137, 173.9999), (174, 349.9999), (350, 399.9999), (400, 469.9999),
              (470, 1300))  # fmt: skip
EMPTY_ATTR = 0x0F  # what the radio stores for an empty slot


def u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 4], "little")


def wide_rx(image: bytes) -> bool:
    return bool(image[BUILD_OPTIONS + 1] & 0x02)  # ENABLE_WIDE_RX


def find_band(hz: int, wide: bool) -> int:
    mhz_value = hz / 1e6
    for band, (lo, hi) in enumerate(BANDS_WIDE if wide else BANDS_STANDARD):
        if lo <= mhz_value <= hi:
            return band
    raise CodeplugError(f"{mhz_value} MHz is outside the radio's bands")


# --- tones ------------------------------------------------------------------------


def decode_tone(flag: int, code: int) -> Tone:
    if flag == 0:
        return "off"
    if flag == 1:
        return CTCSS[code] if code < len(CTCSS) else f"?C{code}"
    if flag in (2, 3):
        return f"D{DCS[code]:03d}{'N' if flag == 2 else 'I'}" if code < len(DCS) else f"?D{code}"
    return f"?F{flag}"


def encode_tone(tone: Tone) -> tuple[int, int]:
    if tone == "off":
        return 0, 0
    if isinstance(tone, float | int):
        if round(tone, 1) not in CTCSS:
            raise CodeplugError(f"CTCSS {tone} Hz is not in the radio's tone list")
        return 1, CTCSS.index(round(tone, 1))
    code, polarity = int(tone[1:4]), tone[4]
    if code not in DCS:
        raise CodeplugError(f"DCS code {tone} is not supported by the radio")
    return (2 if polarity == "N" else 3), DCS.index(code)


# --- channels ---------------------------------------------------------------------


def decode_scan_lists(attr: int) -> list[int]:
    bits = attr >> 5
    return [n for n in (1, 2, 3) if bits & (1 << (n - 1))]


def decode_channel(rec: bytes, attr: int, name: bytes, slot: int) -> Channel | None:
    freq = u32(rec, 0)
    if freq in (0, 0xFFFFFFFF):
        return None
    rx = freq * 10
    offset = u32(rec, 4) * 10
    direction = rec[11] & 0x0F
    tx = rx + offset if direction == 1 else rx - offset if direction == 2 else rx
    modulation = rec[11] >> 4
    b12, b13 = rec[12], rec[13]
    extra: dict = {}
    if b12 & 0x40:
        extra["tx_lock"] = True
    if b12 & 0x20:
        extra["busy_lock"] = True
    if b12 & 0x01:
        extra["reverse"] = True
    if (b13 >> 1) & 0x07:
        extra["ptt_id"] = (b13 >> 1) & 0x07
    if b13 & 0x01:
        extra["dtmf_decode"] = True
    if rec[15]:
        extra["scrambler"] = rec[15]
    if (attr >> 3) & 0x03:
        extra["compander"] = (attr >> 3) & 0x03
    step = STEPS_KHZ[rec[14]] if rec[14] < len(STEPS_KHZ) else rec[14]
    if step != DEFAULT_STEP:
        extra["step"] = step
    lists = decode_scan_lists(attr)
    if lists not in ([], [1]):
        extra["scan_lists"] = lists
    end = next((i for i, b in enumerate(name) if b in (0x00, 0xFF)), len(name))
    return Channel(
        slot=slot,
        rx=rx,
        tx=tx,
        name=name[:end].decode("ascii", errors="replace").rstrip(),
        mode=MODES[modulation] if modulation < len(MODES) else f"MOD{modulation}",
        bw="narrow" if b12 & 0x02 else "wide",
        power=POWER_LEVELS[(b12 >> 2) & 0x07],
        rx_tone=decode_tone(rec[10] & 0x0F, rec[8]),
        tx_tone=decode_tone(rec[10] >> 4, rec[9]),
        scan=bool(lists),
        extra=extra,
    )


EXTRA_KEYS = {
    "tx_lock": (bool, None),
    "busy_lock": (bool, None),
    "reverse": (bool, None),
    "ptt_id": (int, 4),
    "dtmf_decode": (bool, None),
    "scrambler": (int, 10),
    "compander": (int, 3),
    "step": (float, None),
    "scan_lists": (list, None),
}


def _scan_lists(ch: Channel) -> list[int]:
    lists = ch.extra.get("scan_lists", [1] if ch.scan else [])
    if not isinstance(lists, list) or any(n not in (1, 2, 3) for n in lists):
        raise CodeplugError("extra.scan_lists must be a list of 1, 2, 3")
    if bool(lists) != ch.scan:
        raise CodeplugError("scan: true needs at least one scan list, scan: false needs none")
    return sorted(set(lists))


def encode_channel(image: bytearray, index: int, old: Channel | None, new: Channel, wide: bool) -> None:
    """Patch channel ``index`` (0-based): record, attribute byte and name."""
    rec = bytearray(image[index * 16 : index * 16 + 16] if old else b"\x00" * 16)
    attr = image[ATTR_OFFSET + index] if old else 0x00
    check_extra(new, EXTRA_KEYS)  # step and scan_lists are checked where they are encoded
    check_common(old, new, POWER_LEVELS, MODES)
    if new.tx is None:
        raise CodeplugError("duplex: off is not supported on the UV-K5 – use extra: {tx_lock: true}")
    if changed(old, new, "rx"):
        if new.rx % 10:
            raise CodeplugError(f"{new.rx / 1e6} MHz: frequencies are stored in 10 Hz steps")
        rec[0:4] = (new.rx // 10).to_bytes(4, "little")
        attr = (attr & 0xF8) | find_band(new.rx, wide)
    if changed(old, new, "tx") or changed(old, new, "rx"):
        diff = new.tx - new.rx
        if diff % 10:
            raise CodeplugError("the TX offset must be a multiple of 10 Hz")
        rec[4:8] = (abs(diff) // 10).to_bytes(4, "little")
        set_bits(rec, 11, 0x0F, 0, 0 if diff == 0 else 1 if diff > 0 else 2)
    if changed(old, new, "rx_tone"):
        flag, code = encode_tone(new.rx_tone)
        rec[8] = code
        set_bits(rec, 10, 0x0F, 0, flag)
    if changed(old, new, "tx_tone"):
        flag, code = encode_tone(new.tx_tone)
        rec[9] = code
        set_bits(rec, 10, 0x0F, 4, flag)
    if changed(old, new, "mode"):
        set_bits(rec, 11, 0x0F, 4, MODES.index(new.mode))
    if changed(old, new, "bw"):
        set_bit(rec, 12, 0x02, new.bw == "narrow")
    if changed(old, new, "power"):
        set_bits(rec, 12, 0x07, 2, POWER_LEVELS.index(new.power))
    for key, mask in (("tx_lock", 0x40), ("busy_lock", 0x20), ("reverse", 0x01)):
        if extra_changed(old, new, key, False):
            set_bit(rec, 12, mask, new.extra.get(key, False))
    if extra_changed(old, new, "ptt_id", 0):
        set_bits(rec, 13, 0x07, 1, new.extra.get("ptt_id", 0))
    if extra_changed(old, new, "dtmf_decode", False):
        set_bit(rec, 13, 0x01, new.extra.get("dtmf_decode", False))
    if extra_changed(old, new, "step", DEFAULT_STEP):
        step = new.extra.get("step", DEFAULT_STEP)
        if step not in STEPS_KHZ:
            raise CodeplugError(f"extra.step {step} kHz is not one of {', '.join(map(str, STEPS_KHZ))}")
        rec[14] = STEPS_KHZ.index(step)
    if extra_changed(old, new, "scrambler", 0):
        rec[15] = new.extra.get("scrambler", 0)
    if extra_changed(old, new, "compander", 0):
        attr = (attr & ~0x18 & 0xFF) | (new.extra.get("compander", 0) & 0x03) << 3
    if old is None or old.scan != new.scan or old.extra.get("scan_lists") != new.extra.get("scan_lists"):
        bits = sum(1 << (n - 1) for n in _scan_lists(new))
        attr = (attr & 0x1F) | bits << 5
    image[index * 16 : index * 16 + 16] = rec
    image[ATTR_OFFSET + index] = attr
    if changed(old, new, "name"):
        try:
            raw = new.name.encode("ascii")
        except UnicodeEncodeError:
            raise CodeplugError(f"name {new.name!r}: the UV-K5 supports only ASCII characters") from None
        if len(raw) > 10:
            raise CodeplugError(f"name {new.name!r} is {len(raw)} characters long, the radio allows 10")
        # A renamed channel: only the 10 bytes the firmware shows, bytes 10–15 stay
        # untouched; a new channel gets the whole (empty) slot like CHIRP writes it.
        size = 16 if old is None else 10
        image[NAME_OFFSET + index * 16 : NAME_OFFSET + index * 16 + size] = raw.ljust(size, b"\x00")


def delete_channel(image: bytearray, index: int) -> None:
    image[index * 16 : index * 16 + 16] = b"\xff" * 16
    image[NAME_OFFSET + index * 16 : NAME_OFFSET + index * 16 + 16] = b"\xff" * 16
    image[ATTR_OFFSET + index] = EMPTY_ATTR


# --- settings ---------------------------------------------------------------------

ON_OFF = ("off", "on")
SETTINGS = (
    Setting("call_channel", 0xE70, "call channel (1–200)", "zone", mask=0xFF),
    Setting("squelch", 0xE71, "squelch 0–9", mask=0xFF),
    Setting("noaa_autoscan", 0xE73, "NOAA auto scan", "bool", mask=0x01),
    Setting("key_lock", 0xE74, "keypad locked", "bool", mask=0x01, shift=0),
    Setting("menu_lock", 0xE74, "menu lock (F4HWN)", "bool", mask=0x01, shift=1),
    Setting("set_key", 0xE74, "SetKey (F4HWN)", "choice", ("menu", "key_up", "key_down", "key_exit", "key_star"),
            mask=0x0F, shift=2),
    Setting("vox", 0xE75, "VOX", "bool", mask=0xFF),
    Setting("vox_level", 0xE76, "VOX level (0 = 1 … 9 = 10)", mask=0xFF),
    Setting("mic_gain", 0xE77, "microphone gain", "choice", ("+1.1dB", "+4.0dB", "+8.0dB", "+12.0dB", "+15.1dB"),
            mask=0xFF),
    Setting("backlight_min", 0xE78, "backlight level min 0–10", mask=0x0F, shift=4),
    Setting("backlight_max", 0xE78, "backlight level max 0–10", mask=0x0F, shift=0),
    Setting("channel_display", 0xE79, "channel display", "choice", ("frequency", "number", "name", "name+freq"),
            mask=0xFF),
    Setting("crossband", 0xE7A, "cross band (raw)", "number", mask=0xFF),
    Setting("battery_save", 0xE7B, "battery save", "choice", ("off", "1:1", "1:2", "1:3", "1:4", "1:5"), mask=0xFF),
    Setting("dual_watch", 0xE7C, "dual watch (raw)", "number", mask=0xFF),
    Setting("backlight_time", 0xE7D, "backlight time (0 = off, 1 = 5 s, … index)", "number", mask=0xFF),
    Setting("ste", 0xE7E, "squelch tail elimination", "bool", mask=0x01, shift=0),
    Setting("nfm", 0xE7E, "narrow FM width (F4HWN)", "choice", ("narrow", "narrower"), mask=0x03, shift=1),
    Setting("button_beep", 0xE90, "key beep", "bool", mask=0x01, shift=0),
    Setting("key_m_long", 0xE90, "M long press – action number", "number", mask=0x7F, shift=1),
    Setting("key1_short", 0xE91, "side key 1 short – action number", "number", mask=0xFF),
    Setting("key1_long", 0xE92, "side key 1 long – action number", "number", mask=0xFF),
    Setting("key2_short", 0xE93, "side key 2 short – action number", "number", mask=0xFF),
    Setting("key2_long", 0xE94, "side key 2 long – action number", "number", mask=0xFF),
    Setting("scan_resume", 0xE95, "scan resume mode (index)", "number", mask=0xFF),
    Setting("auto_keypad_lock", 0xE96, "auto keypad lock (index)", "number", mask=0xFF),
    Setting("power_on_display", 0xE97, "power-on display", "choice", ("all", "sound", "message", "voltage", "none"),
            mask=0xFF),
    Setting("voice", 0xEA0, "voice prompts", "choice", ("off", "chinese", "english"), mask=0xFF),
    Setting("alarm_mode", 0xEA8, "alarm mode", "choice", ("site", "tone"), mask=0xFF),
    Setting("roger", 0xEA9, "roger", "choice", ("off", "roger", "mdc"), mask=0xFF),
    Setting("repeater_tail", 0xEAA, "repeater tail elimination (0 = off, n × 100 ms)", mask=0xFF),
    Setting("tx_vfo", 0xEAB, "TX VFO", "choice", ("A", "B"), mask=0xFF),
    Setting("battery_type", 0xEAC, "battery type", "choice", ("1600mAh", "2200mAh", "3500mAh"), mask=0xFF),
    Setting("freq_lock", 0xF40, "TX frequency lock (F-LOCK index)", "number", mask=0xFF),
    Setting("backlight_tx_rx", 0xF47, "backlight on TX/RX", "choice", ("off", "tx", "rx", "tx/rx"), mask=0x03,
            shift=6),
    Setting("am_fix", 0xF47, "AM fix", "bool", mask=0x01, shift=5),
    Setting("mic_bar", 0xF47, "microphone bar", "bool", mask=0x01, shift=4),
    Setting("battery_text", 0xF47, "battery text", "choice", ("none", "voltage", "percent"), mask=0x03, shift=2),
    Setting("live_dtmf_decoder", 0xF47, "live DTMF decoder", "bool", mask=0x01, shift=1),
    Setting("off_timer", 0x1FF4, "power-off timer (F4HWN, minutes index)", mask=0x7F, shift=1),
    Setting("timer", 0x1FF4, "timer (F4HWN)", "bool", mask=0x01, shift=0),
    Setting("gui_style", 0x1FF5, "GUI style (F4HWN)", "choice", ("tiny", "classic"), mask=0x01, shift=7, ff_unset=False),
    Setting("s_meter", 0x1FF5, "S-meter style (F4HWN)", "choice", ("tiny", "classic"), mask=0x01, shift=6, ff_unset=False),
    Setting("lock_mode", 0x1FF5, "lock (F4HWN)", "choice", ("keys", "keys+ptt"), mask=0x01, shift=5, ff_unset=False),
    Setting("invert_display", 0x1FF5, "inverted display (F4HWN)", "bool", mask=0x01, shift=4, ff_unset=False),
    Setting("contrast", 0x1FF5, "contrast 0–15 (F4HWN)", mask=0x0F, shift=0, ff_unset=False),
    Setting("tot_alert", 0x1FF6, "TX time-out alert (F4HWN)", "choice", ("off", "sound", "visual", "all"),
            mask=0x0F, shift=4),
    Setting("eot_alert", 0x1FF6, "end-of-TX alert (F4HWN)", "choice", ("off", "sound", "visual", "all"),
            mask=0x0F, shift=0),
    Setting("user_power", 0x1FF7, "power of level 'user' (F4HWN SetPwr)", "choice",
            ("<20mW", "125mW", "250mW", "500mW", "1W", "2W", "5W"), mask=0x0F, shift=4),
    Setting("ptt_mode", 0x1FF7, "PTT mode (F4HWN)", "choice", ("classic", "onepush"), mask=0x0F, shift=0),
)  # fmt: skip

BUILD_FLAGS = (
    (0, 7, "dtmf_calling"), (0, 6, "power_on_password"), (0, 5, "tx_1750"), (0, 4, "alarm"), (0, 3, "vox"),
    (0, 2, "voice"), (0, 1, "noaa"), (0, 0, "fm_radio"), (1, 6, "rescue_ops"), (1, 5, "bandscope"),
    (1, 4, "am_fix"), (1, 3, "game"), (1, 2, "raw_demodulators"), (1, 1, "wide_rx"), (1, 0, "flashlight"),
)  # fmt: skip


def decode_image(image: bytes, driver_id: str) -> Codeplug:
    cp = Codeplug(driver=driver_id)
    zone = Zone(number=1, size=CHANNEL_COUNT, first_slot=1)
    for i in range(CHANNEL_COUNT):
        ch = decode_channel(
            image[i * 16 : i * 16 + 16], image[ATTR_OFFSET + i], image[NAME_OFFSET + i * 16 : NAME_OFFSET + i * 16 + 10],
            i + 1,
        )  # fmt: skip
        if ch:
            zone.channels.append(ch)
    cp.zones = [zone]
    wide = wide_rx(image)
    for n in range(VFO_SLOTS):
        idx = CHANNEL_COUNT + n
        lo, hi = (BANDS_WIDE if wide else BANDS_STANDARD)[n // 2]
        name = f"F{n // 2 + 1}{'AB'[n % 2]}"
        attr = image[ATTR_OFFSET + CHANNEL_COUNT + n // 2]
        ch = decode_channel(image[idx * 16 : idx * 16 + 16], attr & 0x1F, b"", 0)
        values = (
            {}
            if ch is None
            else {k: v for k, v in plain(ch.to_yaml()).items() if k not in ("slot", "name", "scan")}
        )
        cp.vfo[name] = Section(values=values or {"band": f"{lo:g}-{hi:g}"})
    cp.settings = decode_settings(SETTINGS, image)
    features = Section()
    for byte, bit, key in BUILD_FLAGS:
        features.set(key, bool(image[BUILD_OPTIONS + byte] >> bit & 1))
    cp.extra["firmware_features"] = features
    fm = []
    for n in range(20):
        value = int.from_bytes(image[FM_OFFSET + n * 2 : FM_OFFSET + n * 2 + 2], "little")
        if value not in (0, 0xFFFF):
            fm.append({"n": n + 1, "freq": mhz(value * 100_000)})
    cp.extra["broadcast"] = Section(
        values={"fm": fm}, comments={"fm": "FM memories 1–20, MHz, 100 kHz raster (no names)"}
    )
    cp.extra["welcome"] = Section(
        values={
            "line1": text(image[0xEB0:0xEC0].split(b"\x00")[0].decode("ascii", "replace").strip("\xff")),
            "line2": text(image[0xEC0:0xED0].split(b"\x00")[0].decode("ascii", "replace").strip("\xff")),
        },
        comments={"line1": "power-on message (read only for now)"},
    )
    cp.notes = [
        "Quansheng UV-K5 with F4HWN firmware v4.x: 200 channels without zones – zone 1 = all memories.",
        f"Power levels: {POWER_WATTS}.",
        "scan: true = in scan list 1 unless extra.scan_lists says otherwise; the radio has no zones.",
        "vfo, firmware_features and welcome are read only for now.",
        "Omitted rx_tone/tx_tone = off, omitted extra = defaults (step 12.5 kHz). null = not set (FF).",
    ]
    return cp


# --- encoding the whole image -------------------------------------------------------


FM_SLOTS = 20
FM_RANGE = (76.0, 108.0)  # MHz, like CHIRP


def encode_fm(image: bytearray, current: Codeplug, wanted) -> None:
    """broadcast.fm: u16 LE in 100 kHz units at 0x0E40 + 2n, empty = FFFF."""
    new = fm_entries(wanted, FM_SLOTS, 0, *FM_RANGE)
    have = {e["n"]: e["freq"] for e in current.extra["broadcast"].values["fm"]}
    for n in range(1, FM_SLOTS + 1):
        entry = new.get(n)
        if entry is None and n not in have or entry is not None and entry["freq"] == have.get(n):
            continue
        offset = FM_OFFSET + (n - 1) * 2
        if entry is None:
            image[offset : offset + 2] = b"\xff\xff"
            continue
        units = round(entry["freq"] * 10)
        if abs(units - entry["freq"] * 10) > 1e-6:
            raise CodeplugError(f"broadcast.fm n = {n}: {entry['freq']} MHz is not on the 100 kHz raster")
        image[offset : offset + 2] = units.to_bytes(2, "little")


def encode_image(cp: Codeplug, base: bytes) -> bytes:
    """Channels need three places (record, attribute, name), so they are patched
    here; settings and the unsupported-section checks reuse the Radtel encoder."""
    wide = wide_rx(base)
    out = bytearray(base)
    current = decode_image(base, cp.driver)
    broadcast = cp.extra.get("broadcast")
    if isinstance(broadcast, Section) and "fm" in broadcast.values:
        encode_fm(out, current, broadcast.values["fm"])
    if cp.zones_given and cp.zones:  # "zones: []" lists no zone → channels stay as they are
        if [z.number for z in cp.zones] != [1]:
            raise CodeplugError("the UV-K5 has no zones – use only 'zone: 1'")
        wanted = {ch.slot: ch for z in cp.zones for ch in z.channels}
        have = {ch.slot: ch for ch in current.zones[0].channels}
        for slot in sorted(set(wanted) | set(have)):
            if slot > CHANNEL_COUNT:
                raise CodeplugError(f"zone 1: slot {slot} does not exist (max {CHANNEL_COUNT})")
            old, new = have.get(slot), wanted.get(slot)
            if old == new:
                continue
            try:
                if new is None:
                    delete_channel(out, slot - 1)
                else:
                    encode_channel(out, slot - 1, old, new, wide)
            except CodeplugError as e:
                raise CodeplugError(f"zone 1 slot {slot}: {e}") from None
    # Settings + read-only sections: reuse the generic patcher without channels.
    rest = Codeplug(driver=cp.driver, vfo=cp.vfo, settings=cp.settings, extra=cp.extra, zones_given=False)
    return patch_image(bytes(out), rest, IMAGE_MAP)


IMAGE_MAP = ImageMap(
    decode=lambda image: decode_image(image, "quansheng-uvk5-f4hwn"),
    encode_channel=lambda *_: None,  # channels are handled in encode_image
    new_record=b"",
    settings=SETTINGS,
    settings_offset=0,
    settings_length=IMAGE_SIZE,
    encoded_extra=("broadcast.fm",),
)
