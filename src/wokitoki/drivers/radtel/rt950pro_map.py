"""RT-950 Pro memory image layout and decoding into a Codeplug.

Ported from the RT950Pro macOS app (Model/*.swift, docs/MEMORY_MAP.md).
The image is the concatenation of the clone segments (33 152 B), byte for
byte the same as the app's .img backups. See docs/radios/radtel-rt950pro.md.
"""

from __future__ import annotations

from wokitoki.core.codeplug import Channel, Codeplug, CodeplugError, Section, Tone, Zone, mhz, text

from .codecs import (  # noqa: F401 – re-exported for tests and callers
    DCS_BASE,
    DCS_CODES,
    Setting,
    decode_ascii,
    decode_bcd_frequency,
    decode_digit_frequency,
    decode_settings,
    decode_text,
    encode_bcd_frequency,
    encode_text,
    i16,
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
    fm_entries,
    patch_image,
    set_bit,
    set_bits,
)

CHANNEL_COUNT = 960
CHANNEL_SIZE = 32
ZONE_COUNT = 10
CHANNELS_PER_ZONE = 99  # verified on the radio; the last zone has only 69

VFO_OFFSET = CHANNEL_COUNT * CHANNEL_SIZE  # 0x7800 ← radio 0x8000
FUNCTION_OFFSET = VFO_OFFSET + 0x100  # ← 0x9000
DTMF_OFFSET = FUNCTION_OFFSET + 0x100  # ← 0xA000
MODULATION_OFFSET = DTMF_OFFSET + 0x200  # ← 0xB000
MODULATION_NAMES_OFFSET = MODULATION_OFFSET + 0x200  # ← 0xD000
APRS_OFFSET = MODULATION_NAMES_OFFSET + 0x300  # ← APRS space 0x0000
IMAGE_SIZE = APRS_OFFSET + 0x80  # 33 152

POWER_LEVELS = ("high", "mid", "low")


# --- primitive codecs -------------------------------------------------------------


DTMF_DIGITS = "0123456789ABCD*#"


def decode_dtmf(b: bytes) -> str:
    out = []
    for x in b:
        if x == 0xFF:
            break
        if x < len(DTMF_DIGITS):
            out.append(DTMF_DIGITS[x])
    return "".join(out)


def decode_fhss(b: bytes) -> str | None:
    """3 B code (reversed) + 0xA0 marker; anything else = no FHSS."""
    if len(b) != 4 or b[3] != 0xA0:
        return None
    return b[2::-1].hex().upper()


def decode_tone(first: int, second: int) -> Tone:
    """RT-950 Pro: 00 00 = off; second byte 00 → DCS, first byte = 1-based
    index into DCS_CODES; otherwise CTCSS, little-endian in 0.1 Hz."""
    if (first, second) in ((0, 0), (0xFF, 0xFF)):
        return "off"
    if second == 0:
        if 1 <= first <= len(DCS_CODES):
            return DCS_CODES[first - 1]
        return f"?{first:02X}{second:02X}"
    return ((second << 8) | first) / 10


def encode_tone(tone: Tone) -> bytes:
    if tone == "off":
        return b"\x00\x00"
    if isinstance(tone, float | int):
        return round(tone * 10).to_bytes(2, "little")
    if tone not in DCS_CODES:
        raise CodeplugError(f"DCS code {tone} is not supported by the radio")
    return bytes([DCS_CODES.index(tone) + 1, 0])


EXTRA_KEYS = {
    "signal_group": (int, 15),
    "ptt_id": (int, 15),
    "scrambler": (int, 15),
    "encryption": (int, 3),
    "busy_lock": (bool, None),
    "learn_fhss": (bool, None),
    "fhss": (str, None),
}
# Like a channel created by the radio itself (RT950Pro Channel.new): FHSS 00 00 00 00.
NEW_RECORD = b"\xff" * 12 + b"\x00" * 8 + b"\xff" * 12


def encode_channel(r: bytearray, old: Channel | None, new: Channel) -> None:
    """Mirror of decode_channel; patches only fields that differ from ``old``."""
    check_common(old, new, POWER_LEVELS)
    check_extra(new, EXTRA_KEYS)
    if changed(old, new, "rx"):
        r[0:4] = encode_bcd_frequency(new.rx)
    if changed(old, new, "tx"):
        # TX disabled keeps the stored TX frequency (a new channel gets RX).
        if new.tx is not None:
            r[4:8] = encode_bcd_frequency(new.tx)
        elif old is None:
            r[4:8] = encode_bcd_frequency(new.rx)
        set_bit(r, 15, 0x02, new.tx is not None)
    if changed(old, new, "rx_tone"):
        r[8:10] = encode_tone(new.rx_tone)
    if changed(old, new, "tx_tone"):
        r[10:12] = encode_tone(new.tx_tone)
    if extra_changed(old, new, "signal_group", 0):
        set_bits(r, 12, 0x0F, 0, new.extra.get("signal_group", 0))
    if extra_changed(old, new, "ptt_id", 0):
        set_bits(r, 13, 0x0F, 0, new.extra.get("ptt_id", 0))
    if changed(old, new, "power"):
        set_bits(r, 14, 0x0F, 0, POWER_LEVELS.index(new.power))
    if extra_changed(old, new, "scrambler", 0):
        set_bits(r, 14, 0x0F, 4, new.extra.get("scrambler", 0))
    if extra_changed(old, new, "learn_fhss", False):
        set_bit(r, 15, 0x80, new.extra.get("learn_fhss", False))
    if changed(old, new, "bw"):
        set_bit(r, 15, 0x40, new.bw == "narrow")
    if extra_changed(old, new, "encryption", 0):
        set_bits(r, 15, 0x03, 4, new.extra.get("encryption", 0))
    if extra_changed(old, new, "busy_lock", False):
        set_bit(r, 15, 0x08, new.extra.get("busy_lock", False))
    if changed(old, new, "scan"):
        set_bit(r, 15, 0x04, new.scan)
    if changed(old, new, "mode"):
        set_bit(r, 15, 0x01, new.mode == "AM")
    if extra_changed(old, new, "fhss", None):
        value = fhss_value(new.extra.get("fhss"))
        # "No FHSS" is 00 00 00 00 – what the radio writes itself (the reference writes FF).
        r[16:20] = b"\x00" * 4 if value is None else value.to_bytes(3, "little") + b"\xa0"
    if changed(old, new, "name"):
        r[20:32] = encode_text(new.name, 12)


# --- channels and zones -----------------------------------------------------------


def decode_channel(b: bytes, slot: int) -> Channel | None:
    rx = decode_bcd_frequency(b[0:4])
    if rx is None:
        return None
    tx = decode_bcd_frequency(b[4:8]) or rx
    flags = b[15]
    extra: dict = {}
    if b[12] & 0x0F:
        extra["signal_group"] = b[12] & 0x0F
    if b[13] & 0x0F:
        extra["ptt_id"] = b[13] & 0x0F
    if b[14] >> 4:
        extra["scrambler"] = b[14] >> 4
    if (flags >> 4) & 0x03:
        extra["encryption"] = (flags >> 4) & 0x03
    if flags & 0x08:
        extra["busy_lock"] = True
    if flags & 0x80:
        extra["learn_fhss"] = True
    fhss = decode_fhss(b[16:20])
    if fhss:
        extra["fhss"] = fhss
    return Channel(
        slot=slot,
        rx=rx,
        tx=tx if flags & 0x02 else None,
        name=decode_text(b[20:32]),
        mode="AM" if flags & 0x01 else "FM",
        bw="narrow" if flags & 0x40 else "wide",
        power=POWER_LEVELS[min(b[14] & 0x0F, 2)],
        rx_tone=decode_tone(b[8], b[9]),
        tx_tone=decode_tone(b[10], b[11]),
        scan=bool(flags & 0x04),
        extra=extra,
    )


def decode_zones(image: bytes) -> list[Zone]:
    zones = []
    for z in range(ZONE_COUNT):
        first = z * CHANNELS_PER_ZONE
        size = min(CHANNELS_PER_ZONE, CHANNEL_COUNT - first)
        zone = Zone(number=z + 1, size=size, first_slot=first + 1)
        for pos in range(size):
            start = (first + pos) * CHANNEL_SIZE
            channel = decode_channel(image[start : start + CHANNEL_SIZE], pos + 1)
            if channel is not None:
                zone.channels.append(channel)
        zones.append(zone)
    return zones


# --- VFO ----------------------------------------------------------------------------

SHIFTS = ("simplex", "+", "-", "split")
VFO_BANDS = ("50-76", "108-136", "137-174", "174-350", "350-400", "400-470", "470-600")
STEPS_KHZ = (2.5, 5, 6.25, 10, 12.5, 25)


def decode_vfo(b: bytes) -> Section:
    s = Section()
    s.set("rx", mhz(decode_digit_frequency(b[0:8], 10)))
    s.set("shift", pick(SHIFTS, (b[14] >> 4) & 0x03))
    s.set("offset", mhz(decode_digit_frequency(b[20:27], 100)))
    s.set("rx_tone", decode_tone(b[8], b[9]))
    s.set("tx_tone", decode_tone(b[10], b[11]))
    s.set("mode", "AM" if b[17] & 0x01 else "FM")
    s.set("bw", "narrow" if b[17] & 0x40 else "wide")
    # The reference driver always reads VFO power as High – that is a bug there.
    s.set("power", POWER_LEVELS[min(b[16] & 0x0F, 2)])
    s.set("step", pick(STEPS_KHZ, b[19] & 0x0F))
    s.set("band", pick(VFO_BANDS, b[18] & 0x0F))
    extra = {}
    if b[14] & 0x0F:
        extra["signal_group"] = b[14] & 0x0F
    if b[16] >> 4:
        extra["scrambler"] = b[16] >> 4
    if (b[17] >> 4) & 0x03:
        extra["encryption"] = (b[17] >> 4) & 0x03
    if b[13] & 0x01:
        extra["busy_lock"] = True
    if b[17] & 0x80:
        extra["learn_fhss"] = True
    if extra:
        s.set("extra", extra)
    return s


# --- byte-mapped settings -----------------------------------------------------------


FUNCTION_SETTINGS = (
    Setting("sql", 0x00, "squelch 0–9"),
    Setting("save_mode", 0x01, "battery save 0–3"),
    Setting("vox", 0x02, "VOX sensitivity 0–9"),
    Setting("auto_backlight", 0x03, "backlight 0–9"),
    Setting("tdr", 0x04, "dual watch (TDR)", "bool"),
    Setting("tot", 0x05, "transmit time-out 0–9"),
    Setting("beep_prompt", 0x06, "key beep", "bool"),
    Setting("voice_prompt", 0x07, "voice prompts – language order unverified", "choice", ("off", "english", "chinese")),
    Setting("language", 0x08, "menu language", "choice", ("english", "chinese", "other")),
    Setting("dtmf_mode", 0x09, "DTMF mode", "choice", ("off", "dt-st", "ani-id", "dtmf")),
    Setting("scan_mode", 0x0A, "scan mode", "choice", ("time", "carrier", "search")),
    Setting("ptt_id", 0x0B, "PTT ID", "choice", ("off", "bot", "eot", "both")),
    Setting("send_id_delay", 0x0C, "ID send delay 0–9"),
    Setting("display_mode_a", 0x0D, "display A", "choice", ("channel", "frequency", "name")),
    Setting("display_mode_b", 0x0E, "display B", "choice", ("channel", "frequency", "name")),
    Setting("display_mode_c", 0x0F, "display C", "choice", ("channel", "frequency", "name")),
    Setting("auto_key_lock", 0x10, "auto key lock", "bool"),
    Setting("alarm_mode", 0x11, "alarm mode", "choice", ("local", "remote", "tone")),
    Setting("alarm_sound", 0x12, "alarm sound", "choice", ("off", "type1", "type2")),
    Setting("tail_noise_clear", 0x14, "tail noise clear", "bool"),
    Setting("pass_repeater_noise_clear", 0x15, "repeater noise clear", "bool"),
    Setting("pass_repeater_noise_detect", 0x16, "repeater noise detect", "bool"),
    Setting("sound_tx_end", 0x17, "roger beep", "bool"),
    Setting("current_work_mode", 0x18, "active channel", "choice", ("A", "B", "C")),
    Setting("fm_radio", 0x19, "FM radio", "bool"),
    Setting("work_mode_a", 0x1A, "work mode A (2–3 unverified)", "choice",
            ("channel", "frequency", "mode2", "mode3"), mask=0x03, shift=0),
    Setting("work_mode_b", 0x1A, "work mode B", "choice",
            ("channel", "frequency", "mode2", "mode3"), mask=0x03, shift=2),
    Setting("work_mode_c", 0x1A, "work mode C", "choice",
            ("channel", "frequency", "mode2", "mode3"), mask=0x03, shift=4),
    Setting("lock_keyboard", 0x1B, "keypad lock", "bool"),
    Setting("power_on_message", 0x1C, "power-on screen", "choice", ("full", "message", "voltage")),
    Setting("bt_write_switch", 0x1D, "Bluetooth", "bool"),
    Setting("rtone", 0x1E, "repeater tone (R-Tone), meaning of the number unknown", "number"),
    Setting("vox_delay", 0x20, "VOX delay 0–9"),
    Setting("timer_menu_quit", 0x21, "menu auto-exit 0–9"),
    Setting("weather_channel", 0x25, "weather channel 0–9"),
    Setting("divide_channel", 0x26, "divide channels", "bool"),
    Setting("subaudio_scan_save", 0x27, "save the found sub-tone", "bool"),
    Setting("vox_switch", 0x28, "VOX", "bool"),
    Setting("key_side1_short", 0x29, "side key 1 short – function number", "number"),
    Setting("key_side1_long", 0x2A, "side key 1 long – function number", "number"),
    Setting("key_side2_short", 0x2B, "side key 2 short – function number", "number"),
    Setting("key_side2_long", 0x2C, "side key 2 long – function number", "number"),
    Setting("current_zone_a", 0x2D, "current zone A", "zone"),
    Setting("current_zone_b", 0x2E, "current zone B", "zone"),
    Setting("current_zone_c", 0x2F, "current zone C", "zone"),
    Setting("ab_uv_transfer", 0x39, "A/B sync", "bool"),
    Setting("sound_transfer", 0x3A, "sound transfer", "bool"),
    *(Setting(f"key{n}_long", 0x3B + n, f"key {n} long – function number", "number", mask=0x1F) for n in range(10)),
)  # fmt: skip

APRS_SETTINGS = (
    Setting("aprs_switch", 0x00, "APRS", "bool"),
    Setting("gps_switch", 0x01, "GPS", "bool"),
    Setting("latlon_unit", 0x02, "coordinate unit", "number"),
    Setting("speed_unit", 0x03, "speed", "choice", ("km/h", "mph")),
    Setting("distance_unit", 0x04, "distance", "choice", ("km", "mi")),
    Setting("altitude_unit", 0x05, "altitude", "choice", ("m", "ft")),
    Setting("time_zone", 0x06, "time zone 0–23", mask=0x1F),
    Setting("ssid", 0x17, "SSID 0–15"),
    Setting("routing_select", 0x18, "routing select", "number"),
    Setting("my_position", 0x19, "my position", "number"),
    Setting("radio_symbol", 0x1A, "station symbol", "number", mask=0xFF),
    Setting("user_defined_icon", 0x1B, "custom icon", "number", mask=0x7F),
    Setting("aprs_priority", 0x1D, "priority", "choice", ("low", "normal", "high")),
    Setting("data_tx_delay", 0x1E, "data TX delay 0–9"),
    Setting("aprs_decode_prompt_tone", 0x20, "receive tone", "bool"),
    Setting("aprs_rx_auto_popup", 0x21, "auto popup", "bool"),
    Setting("beacon_tx_type", 0x22, "beacon type", "number"),
    Setting("timed_beacon_time", 0x24, "beacon interval", mask=0xFF),
    Setting("mice_type", 0x26, "Mic-E type", "number"),
    Setting("tnc_data_type", 0x27, "TNC data type", "number"),
    Setting("aprs_forward_channel", 0x28, "forward channel 0–15"),
    Setting("aprs_forward_routing", 0x29, "forward routing", "number"),
    Setting("aprs_wait_forward", 0x2A, "wait before forwarding 0–9"),
    Setting("custom_routing_one_ssid", 0x31, "custom routing 1 – SSID"),
    Setting("custom_routing_two_ssid", 0x38, "custom routing 2 – SSID"),
    Setting("send_custom_messages", 0x4E, "send custom message", "bool"),
)


# --- DTMF, broadcast receivers, APRS ------------------------------------------------


def decode_dtmf_section(b: bytes) -> Section:
    s = Section()
    s.set("id", text(decode_dtmf(b[0:5])), "own DTMF ID")
    s.set("ptt_id_mode", None if b[6] == 0xFF else pick(("off", "bot", "eot", "both"), b[6] & 0x0F))
    groups = []
    for n, start in enumerate(range(32, 384, 16), 1):
        code = decode_dtmf(b[start : start + 6])
        if code:
            groups.append({"n": n, "code": text(code)})
    s.set("groups", groups, "22 groups, max 6 characters")
    return s


def _opt(value: int, modulus: int) -> int | None:
    return None if value == 0xFF else value % modulus


def _scaled(value: int, unit_hz: int) -> int | None:
    return None if value in (0, 0xFFFF) else value * unit_hz


# FM is stored in 10 kHz units (verified: 9250 = 92.5 MHz). AM and SSB are in
# 1 kHz units: values read from our radio (14150, 9770, 17720, 7144) fall into
# the 22/31/16 m broadcast and 40 m amateur bands. The RT950Pro app and the
# reference driver scale them ×10 kHz, which gives nonsense (e.g. 141.5 MHz AM).
FM_UNIT_HZ = 10_000
AM_SSB_UNIT_HZ = 1_000


def decode_broadcast(params: bytes, names: bytes) -> Section:
    s = Section()
    fm, am, ssb = [], [], []
    for i in range(16):
        n = i + 1
        name = decode_text(names[i * 16 : i * 16 + 12])
        if (hz := _scaled(u16(params, i * 2), FM_UNIT_HZ)) is not None:
            fm.append({"n": n, "freq": mhz(hz), "name": text(name)})
        name = decode_text(names[256 + i * 16 : 256 + i * 16 + 12])
        if (hz := _scaled(u16(params, 34 + i * 2), AM_SSB_UNIT_HZ)) is not None:
            am.append({"n": n, "freq": mhz(hz), "name": text(name)})
        base = 69 + i * 5
        name = decode_text(names[512 + i * 16 : 512 + i * 16 + 12])
        if (hz := _scaled(u16(params, base), AM_SSB_UNIT_HZ)) is not None:
            entry = {"n": n, "freq": mhz(hz)}
            bw = params[base + 2]
            if bw not in (0, 0xFF):
                entry["bw"] = bw
            entry["bfo"] = i16(params, base + 3)
            entry["name"] = text(name)
            ssb.append(entry)
    s.set("fm", fm, "FM memories 1–16, MHz; name max 12 bytes, empty → the radio shows Unknown")
    s.set("am", am, "AM (SW/MW), MHz – 1 kHz unit derived from data")
    s.set("ssb", ssb, "SSB, MHz – 1 kHz unit derived from data")
    s.set("fm_current", _opt(params[32], 15))
    s.set("work_mode", _opt(params[33], 2), "Work Band candidate (unverified)")
    s.set("am_current", _opt(params[66], 15))
    s.set("modulation_mode", _opt(params[67], 5))
    s.set("am_rx_gain", _opt(params[68], 37))
    s.set("ssb_current", _opt(params[149], 15))
    s.set("ssb_step", _opt(params[150], 6))
    s.set("am_step", _opt(params[151], 4))
    s.set("ssb_rx_gain", _opt(params[152], 37))
    return s


def _coordinate(b: bytes, deg: int, minutes: int, sec: int, hemi: int) -> str | None:
    if all(b[i] == 0xFF for i in (deg, minutes, sec, hemi)):
        return None

    def part(i: int) -> str:
        return "?" if b[i] == 0xFF else str(b[i])

    side = "?" if b[hemi] == 0xFF else chr(b[hemi])
    return f"{part(deg)}°{part(minutes)}'{part(sec)}\" {side}"


def decode_aprs(b: bytes) -> Section:
    s = decode_settings(APRS_SETTINGS, b)
    s.set("callsign", text(decode_ascii(b[17:23])), "call sign")
    s.set("latitude", _coordinate(b, 9, 8, 10, 7), "latitude")
    s.set("longitude", _coordinate(b, 13, 12, 14, 11), "longitude")
    s.set("altitude", None if b[15] == b[16] == 0xFF else i16(b, 15), "altitude above sea level")
    s.set("custom_routing_1", text(decode_ascii(b[43:49])))
    s.set("custom_routing_2", text(decode_ascii(b[50:56])))
    s.set("message", text(decode_text(b[79:119])), "custom message")
    return s


# --- whole image ----------------------------------------------------------------------


def decode_image(image: bytes, driver_id: str) -> Codeplug:
    cp = Codeplug(driver=driver_id)
    cp.zones = decode_zones(image)
    cp.vfo = {
        name: decode_vfo(image[VFO_OFFSET + i * 32 : VFO_OFFSET + (i + 1) * 32])
        for i, name in enumerate("ABC")
    }
    cp.settings = decode_settings(FUNCTION_SETTINGS, image[FUNCTION_OFFSET : FUNCTION_OFFSET + 96])
    cp.extra["dtmf"] = decode_dtmf_section(image[DTMF_OFFSET : DTMF_OFFSET + 384])
    cp.extra["broadcast"] = decode_broadcast(
        image[MODULATION_OFFSET : MODULATION_OFFSET + 256],
        image[MODULATION_NAMES_OFFSET : MODULATION_NAMES_OFFSET + 0x300],
    )
    cp.extra["aprs"] = decode_aprs(image[APRS_OFFSET : APRS_OFFSET + 0x80])
    cp.notes = [
        "RT-950 Pro: 10 zones × 99 channels (zone 10 has 69). The radio hides channels outside the Work Band.",
        "Omitted rx_tone/tx_tone = off, omitted extra = defaults. null = not set in the radio (FF).",
        "Unknown bytes live only in the .img – it is the base for writing.",
    ]
    return cp


IMAGE_MAP = ImageMap(
    decode=lambda image: decode_image(image, "radtel-rt950pro"),
    encode_channel=encode_channel,
    new_record=NEW_RECORD,
    settings=FUNCTION_SETTINGS,
    settings_offset=FUNCTION_OFFSET,
    settings_length=96,
    encoded_extra=("broadcast.fm",),
)


FM_SLOTS = 16
FM_RANGE = (64.0, 108.0)  # MHz


def encode_fm(image: bytearray, current: Codeplug, wanted) -> None:
    """broadcast.fm: frequency u16 LE in 10 kHz units at 0xB000 + 2n, name
    (GB2312, 12 of 16 bytes) at 0xD000 + 16n; an empty memory is FF."""
    new = fm_entries(wanted, FM_SLOTS, 12, *FM_RANGE)
    have = {e["n"]: e for e in current.extra["broadcast"].values["fm"]}
    for n in range(1, FM_SLOTS + 1):
        old = have.get(n)
        entry = new.get(n)
        f_off = MODULATION_OFFSET + (n - 1) * 2
        n_off = MODULATION_NAMES_OFFSET + (n - 1) * 16
        if entry is None:
            if old is not None:
                image[f_off : f_off + 2] = b"\xff\xff"
                image[n_off : n_off + 12] = b"\xff" * 12
            continue
        if old is None or old["freq"] != entry["freq"]:
            units = round(entry["freq"] * 100)
            if abs(units - entry["freq"] * 100) > 1e-6:
                raise CodeplugError(f"broadcast.fm n = {n}: {entry['freq']} MHz is not on the 10 kHz raster")
            image[f_off : f_off + 2] = units.to_bytes(2, "little")
        if old is None or old["name"] != entry["name"]:
            image[n_off : n_off + 12] = encode_text(entry["name"], 12, what="station name")


def encode_image(cp: Codeplug, base: bytes) -> bytes:
    broadcast = cp.extra.get("broadcast")
    if isinstance(broadcast, Section) and "fm" in broadcast.values:
        image = bytearray(base)
        encode_fm(image, decode_image(base, cp.driver), broadcast.values["fm"])
        base = bytes(image)
    return patch_image(base, cp, IMAGE_MAP)
