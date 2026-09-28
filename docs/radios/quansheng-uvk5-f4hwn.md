# Quansheng UV-K5 (F4HWN firmware v4.x) – driver `quansheng-uvk5-f4hwn`

Protocol and memory map from the F4HWN CHIRP driver
([armel/uv-k5-chirp-driver](https://github.com/armel/uv-k5-chirp-driver),
`uvk5_egzumer_f4hwn_ver_4_2_0.py`, 2025-08-07). **VERIFIED on our unit
(2026-09-27):** hello, reading (twice identical), YAML round trip, writing
(a channel name changed and changed back, both verified by reading back). Implementation: `drivers/quansheng/protocol.py`, `uvk5_map.py`, `uvk5.py`.

The memory map belongs to **F4HWN v4.x**. Stock firmware, egzumer and F4HWN v5
(UV-K5 V3 / K1 with a bigger EEPROM) differ – the driver reads them with a
warning and **refuses to write** unless the hello reports `F4HWN v4.`.

## Connection

- Programming cable only (no Bluetooth): USB-serial (ours: CH340 `1a86:7523`)
  with a Kenwood 2-pin plug, **38400 Bd**, 8N1.
- `wokitoki scan` identifies the radio by its hello answer (under 1 s).

## Protocol

Frame: `AB CD` + payload length (u16 LE) + XOR(payload + CRC16/XMODEM) + `DC BA`.
XOR key (16 B): `16 6C 14 E6 2E 91 0D 40 21 35 D5 40 13 03 E9 80`. The radio's
replies carry no valid CRC (ignored, like CHIRP).
Payload = command (u16 LE) + body length (u16 LE) + body; every command ends
with the fixed "timestamp" `6A 39 57 64`.

| command | body | reply |
|---|---|---|
| 0x0514 hello | timestamp | 0x0515, firmware string at offset 4 (`F4HWN v4.2`); 0x0518 = bootloader mode |
| 0x051B read | offset u16, length u8, 0, timestamp | 0x051C, offset echo at 4, data from offset 8 |
| 0x051D write | offset u16, length u8, 1, timestamp, data | 0x051E, offset echo at 4 |
| 0x05DD reset | – | none – the radio reboots and loads the EEPROM |

While booting after a reset the radio sends a few bytes of noise (ours:
`98 DF 16 7F` ~3 s after the reset) – the receiver skips everything before
`AB CD`, and the verification read after `write` retries.

Read: 0x0000–0x1FFF in 0x80 blocks (64 blocks, 4.5 s). Write (like CHIRP):
0x0000–0x1CFF in 0x80 blocks + 0x1FF2–0x1FFF (F4HWN settings), then reset.
**Never written:** 0x1D00–0x1FF1 (0x1E00+ is calibration), 0x1FF0–0x1FF1
(firmware build flags).

## EEPROM map (image offset == EEPROM address)

| area | content |
|---|---|
| 0x0000 | 214 × 16 B: channels 1–200, then 14 VFO slots (F1A, F1B … F7B) |
| 0x0D60 | 1 B attribute per channel (+7 for VFO bands): b7-5 scan lists 1/2/3, b4-3 compander, b2-0 band |
| 0x0E40 | 20 × u16 FM broadcast memories (100 kHz units) |
| 0x0E70–0x0EAC | main settings (squelch, VOX, backlight, keys, roger, …) |
| 0x0EB0 | power-on message, 2 × 16 characters |
| 0x0ED0 | DTMF |
| 0x0F18 | scan list defaults / priority channels |
| 0x0F40–0x0F47 | F-LOCK, AM fix, battery text, … |
| 0x0F50 | 200 × 16 B channel names (10 ASCII characters used, padded with 00) |
| 0x1C00 | DTMF contacts |
| 0x1E00 | calibration – never written |
| 0x1FF0 | build options (ENABLE_WIDE_RX etc.) – read only |
| 0x1FF2–0x1FFF | F4HWN settings (SetPwr, PTT mode, TOT/EOT, contrast, …) |

Empty channel: record and name 16 × FF, attribute `0x0F` (what our radio stores).

## Channel (16 B)

```
0-3   RX frequency u32 LE, 10 Hz        4-7 offset u32 LE, 10 Hz
8     RX code   9 TX code   (index into 50 CTCSS tones / 104 DCS codes, no 645)
10    b7-4 TX code type, b3-0 RX code type (0 none, 1 CTCSS, 2 DCS N, 3 DCS I)
11    b7-4 modulation (0 FM, 1 AM, 2 USB), b3-0 offset direction (0 none, 1 +, 2 −)
12    b6 TX lock, b5 busy lockout, b4-2 power, b1 narrow, b0 reverse
13    b3-1 PTT ID (0–4), b0 DTMF decode
14    step index (2.5, 5, 6.25, 10, 12.5, 25, 8.33, 0.01 … kHz)
15    scrambler (0 off, 1–10 = 2600–3500 Hz)
```

Power (3 bits): 7 high 5 W, 6 mid 2 W, 5 low5 1 W, 4 low4 500 mW, 3 low3
250 mW, 2 low2 125 mW, 1 low1 < 20 mW, 0 **user** = the level chosen in the
F4HWN "SetPwr" menu (`settings.user_power`, byte 0x1FF7 high nibble).

wokitoki specifics: `scan: true` = in scan list 1 unless `extra.scan_lists`
lists others; `duplex: off` is rejected (use `extra.tx_lock`); a changed RX
frequency also updates the band in the attribute byte (wide or standard
band table depending on the firmware's ENABLE_WIDE_RX flag).

## Observations on our unit (2026-09-27)

- Hello: `F4HWN v4.2`; build options `20 33` → TX 1750, bandscope, AM fix,
  wide RX, flashlight.
- 35 channels (PMR, shared UHF/VHF, airband in AM); PMR on power `user`
  (SetPwr = 125 mW).
- Between two reads only 0x0E7F changed (`freq_mode_allowed`, state the
  radio keeps itself).
- Write test: 58 blocks + reset, verified by reading back. Names written by
  CHIRP are padded with spaces, wokitoki pads with 00 (like the radio's
  own PMR names) – the display is the same.
