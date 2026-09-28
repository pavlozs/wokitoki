# Radtel RT-900 (BT) – driver `radtel-rt900`

Taken from **CHIRP** (`chirp/drivers/radtel_rt900.py`, class `RT900BT`,
firmware V1.20P; handshake from `mml_jc8810.py`, XOR from
`baofeng_uv17Pro.py`). **VERIFIED on our unit (2026-09-26):** handshake and
reading over BLE and over the USB cable; the memory map is still only
checked for plausibility (see "Observations").
Implementation: `drivers/radtel/rt900.py`, map `rt900_map.py`.

## Connection

- CHIRP talks to the RT-900 BT over BLE through `ble-serial` (a virtual COM
  port), i.e. a transparent UART. Assumption: the same module as the
  RT-950 Pro – service **FFE0**, data **FFE1**. **Verify.**
- BLE (VERIFIED 2026-09-27): service FFE0, data FFE1 as assumed. The radio
  **has FF31** and accepts the unlock write, but – unlike the RT-950 Pro – it
  sends no `21 21 …` reply. Whether the unlock is required is not tested (it
  does no harm). Connecting takes ~13 s on macOS (the radio is found by a
  scan first).
- Cable (VERIFIED): generic CH340 USB-serial cable with a Kenwood 2-pin plug
  (`1a86:7523` identifies the chip, not the radio; macOS
  `/dev/cu.usbserial-<location>`), 57600 Bd, 8N1. A full read takes ~30 s
  (970 blocks) without a single retry; two USB reads in a row are
  byte-identical. The port name depends on the USB socket – an alias with
  `--port` breaks when the cable moves to another socket.
- BLE (VERIFIED): `ping --driver auto`, read (970 blocks in 33 s, no retry)
  and a dry-run write (“Nothing to write” against the state written over USB).
  **Real writes over BLE** (name changed and back): 507 blocks all ACKed in
  18 s, the link did **not** drop (CHIRP's wiki says the radio restarts after
  an upload – CHIRP sends no reset either, so probably older firmware),
  verification read OK at the first attempt.
  Mapped areas identical to the USB read; only 0xE000–0xEFFF differs (the
  radio's own log area, 3 731 bytes grew overnight).
- After a write the radio restarts and the BLE connection drops (CHIRP wiki).
- Scan: the RT-950 Pro and the RT-900 both advertise FFE0; without a
  distinguishing name the guess is uncertain (`scan` shows both drivers) –
  `wokitoki ping` decides.

## Clone protocol (per CHIRP)

1. `PROGRAMBT80U` → `06` (CHIRP tries up to 5×, we retry the whole handshake 3×)
2. `F` → 8 B ident (`01 36 01 80 04 00 05 20` or `01 00 01 80 04 00 05 20`)
   followed right away by 8 more bytes (`02 00 02 60 01 03 30 04`) – we read
   16 B at once, an unknown ident only warns
3. **no** `M` command (the model is not reported)
4. fixed frame `SEND 20 01 01` + 18 × `00` (25 B) → `06`. Same scheme as the
   RT-950 Pro: code `0x20` → index 1 → key **`CO 7`** (CHIRP `_crypt(1, …)`).
5. read: `'R' addrHi addrLo 0x40` → `'R' addr 0x40` + 64 B, XOR except for
   addresses ≥ 0xF000 (calibration is not encrypted)
6. write: `'W' addr 0x40` + 64 B (XOR) → `06`. wokitoki writes CHIRP's
   `_ranges` except 0xD000: 0x0000–0x7CFF, 0x8000–0x803F, 0x9000–0x903F,
   0xA000–0xA13F – **VERIFIED over USB 2026-09-26** (507 blocks, all ACKed, ~15 s,
   read-back identical; the radio did not need a restart)
7. end `E`

## Image (`.img`, 62 080 B)

The whole address space 0x0000–0xF27F in 64 B blocks (970 blocks), exactly
like CHIRP (`range(0, 0xF250, 0x40)`) – image offset = radio address.
`wokitoki decode` therefore also reads `.img` files saved by CHIRP (the
metadata at the end is cut off).

| area | content | written by CHIRP |
|---|---|---|
| 0x0000–0x7CDF | 999 channels × 32 B (FW V1.20P; older 512) | yes |
| 0x8000–0x803F | VFO A/B | yes |
| 0x9000–0x903F | settings | yes |
| 0xA000–0xA13F | DTMF (speed 0xA007/8, 15 PTT ID codes from 0xA020) | yes |
| 0xD000 | **hidden radio mode** (FF default, A5 GMRS, 66 PMR, 55 144–146/430–440, 56 Super, 00 Factory) | yes |
| 0xF000–0xF24F | calibration – read yes, **NEVER write** | no |

The RT-900 has **no zones** – the YAML has a single "zone 1" = the whole memory.

## Channel (32 B) – differences from the RT-950 Pro

```
0-3   RX BCD (LSB first, 10 Hz)      empty channel: byte 0 = FF
4-7   TX BCD                          FF FF FF FF = transmit disabled (duplex off)
8-9   RX tone u16 LE   10-11 TX tone u16 LE
      0 / FFFF off, ≥ 0x258 CTCSS ×0.1 Hz, 1–0x69 DCS N (index from 1),
      0x6A+ DCS I (index from 0x6A) – DIFFERENT encoding than the RT-950 Pro
12    low: signalling group
13    b1-0: PTT ID
14    b7-2 scrambler, b1-0 power: 0 High, 1 Mid, 2 Low
15    b7 ?, b6 narrow, b5-4 encryption, b3 BCL, b2 scan, b1 AM, b0 FHSS learning
16-18 FHSS code 24 bit LE (FFFFFF = none), 19 flag (A0 set, FF empty)
20-31 name, 12 characters (FF in the middle = space)
```

Note: CHIRP's `MEM_FORMAT` has the comment "0 = H, 1 = L, 2 = M" for the
power, but the code (`TXPOWER_HIGH/MID/LOW` = 0/1/2 and `get_memory`) uses
**0 High, 1 Mid, 2 Low** – we follow the code. Verify on the radio (an
earlier version of this document wrongly took the order from the comment).
Bit 1 of byte 15 is **AM** on the RT-900 (on the RT-950 Pro "TX enabled"),
bit 0 FHSS learning (on the RT-950 Pro AM). Power: High 8 W, Mid 4 W, Low 1 W.

## VFO (32 B, A and B)

```
0-7   frequency, one digit per byte (10 Hz)
8-9   RX tone   10-11 TX tone (encoded like a channel)
14    b5-4 offset direction (0 off, 1 +, 2 −), low: signalling group
16    b4-2 scrambler, b1-0 power
17    b6 narrow, b5-4 encryption, b1 AM
19    step: 0 2.5 / 1 5 / 2 6.25 / 3 10 / 4 12.5 / 5 20 / 6 25 / 7 50 / 8 8.33 kHz
20-25 offset, one digit per byte (1 kHz)
```

## Settings (0x9000)

Keys and allowed values in `rt900_map.SETTINGS` (the YAML shows them in
comments). Peculiarities: 0x19 FM radio is **inverted** (0 = enabled),
0x1A work mode A (b0) and B (b4), side keys at 0x2A–0x2C (shifted by one on
the BT version).

## Observations on our unit (2026-09-26)

- Ident `01 36 01 80 04 00 05 20 02 00 02 60 01 03 30 04` (known CHIRP
  fingerprint, FW V1.20P), hidden radio mode `super` (0x56).
- BLE read (21:03) vs. USB reads (21:30): all mapped areas identical; 17
  bytes differ in the unmapped area 0xE01C and 0xEE30–0xEE3F (FF in the
  older read, structured values later). The radio writes some state there
  by itself – **never write 0xE000–0xEFFF**, it is not in CHIRP's `_ranges`
  either.
- **The radio logs every programming session itself:** after each write
  (USB or BLE) exactly 66 new bytes appear in 0x8040–0x8FFF right after the
  previous ones (0x899A, 0x89DC, 0x8A1E, … – `FF` turned into mostly `00` with
  a `59 F4` marker), plus a few bytes in 0xE000–0xEFFF. Reads without a write
  do not change it. wokitoki never writes these areas (they are outside the
  write areas and `check_write` refuses them); the verification read compares
  only the written areas. Never restore them from an old backup.
- Airband channels (118–137 MHz) are stored with the AM bit **off**
  (byte 15 = `0x44`: narrow + scan); CHIRP's validator says airband needs AM.
  Check on the radio whether they receive in AM anyway.
- PMR channels are stored with power `2` and UHF/VHF channels with `0`/`2` –
  consistent with CHIRP's code order (0 High, 1 Mid, 2 Low); confirm on the
  display (e.g. channel 1 "PMR1" should show L).
- Slots 255/256 contain unnamed channels 446.02675 / 446.0265 MHz with DCS
  243N and busy lock – created on the radio, real data.

## Secret modes (CHIRP comment)

Power on + PTT + key: `8` Super, `OK` GMRS, `EXIT` Factory,
`2` 144–146/430–440. The value is stored at 0xD000 (`settings.radio_mode`,
wokitoki only reads it for now).
