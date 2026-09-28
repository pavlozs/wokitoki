# Radtel RT-950 Pro – driver `radtel-rt950pro`

Taken over from the sister project `../RT950Pro` (macOS app,
`docs/PROTOCOL.md`, `docs/MEMORY_MAP.md`), where everything was verified on
a real radio (the model reports `RT-950`, firmware with 10 zones).
**VERIFIED** = tried on hardware, otherwise taken from references.

## BLE (VERIFIED)

| | UUID |
|---|---|
| service | `0000FFE0-0000-1000-8000-00805F9B34FB` |
| data (notify + write) | `0000FFE1-…` (read, write, writeNoResp, notify) |
| unlock (write) | `0000FF31-…` – inside the FFE0 service |

Procedure: enable notify on FFE1 → write the unlock to FF31 (with response)
`3F 3F 3F 3F 02 2E 17 1D 5E 57 25 2F 57 13 62 56 04 4B 23 42` →
wait 0.4 s → the radio sends `21 21 21 21 21 20 1B …` (discard) → handshake.
Writes in 20 B chunks; without response with flow control, the APRS block
with response.

## Clone protocol (reading VERIFIED; writing verified on channels in RT950Pro)

1. `PROGRAMBT9000U` → `06`
2. `F` → 16 B ident (our unit: `01 36 01 74 04 00 05 20 02 00 02 60 01 03 30 04`,
   probably a table of TX bands 136–174, 400–520, 200–260 MHz)
3. `M` → 12 B model (`RT-950`)
4. 25 B frame `SEND` + code + 19 random indexes + `00` → `06`; the frame
   itself selects the XOR key (20 candidates), see `drivers/radtel/common.py`
5. the handshake is repeated up to 3× (timeout 2.5 s) – the radio sometimes
   swallows the first exchange; a late duplicate `06` can shift the ident by
   one byte (it is removed)
6. blocks: `[cmd, addrHi, addrLo, 0x80]`, reply 4 B header + 128 B, XOR
7. end `E`

Over BLE from wokitoki (VERIFIED 2026-09-27): two reads in a row identical
(259 blocks, 30–40 s each, no retry), `ping --driver auto`, dry-run and **real
writes** (channel name changed and changed back): 259 blocks all ACKed in
29 s incl. the APRS commit, verification read OK at the first attempt, final
state byte-identical with the state before the test.

wokitoki writes like the RT950Pro app: all 7 segments, ACK per block
(channels 8 s, settings 10 s + 50 ms pause, APRS 30 s + 100 ms, commit last),
END best effort (**not yet tried from wokitoki**).

**The session times out when idle** – do the handshake right before a transfer.
After writing the APRS block (0x58) the radio commits to flash and **drops
BLE by itself**.

| read | write | address | length | content |
|---|---|---|---|---|
| 0x52 | 0x57 | 0x0000 | 0x7800 | 960 channels × 32 B |
| 0x52 | 0x57 | 0x8000 | 0x100 | VFO A/B/C |
| 0x52 | 0x57 | 0x9000 | 0x100 | functions |
| 0x52 | 0x57 | 0xA000 | 0x200 | DTMF |
| 0x52 | 0x57 | 0xB000 | 0x200 | FM/AM/SSB parameters |
| 0x52 | 0x57 | 0xD000 | 0x300 | FM/AM/SSB names |
| 0x54 | 0x58 | 0x0000 | 0x80 | APRS (commit) |

**FM/AM/SSB (0xB000 parameters, 0xD000 names):** FM in 10 kHz units
(VERIFIED: 9250 = 92.5 MHz), **AM and SSB in 1 kHz units** (derived from
our radio's data: 14150, 9770, 17720 = shortwave broadcast 22/31/16 m, SSB
7144 = 40 m band; the reference driver and the Swift app wrongly multiply by
10 kHz). Names are 16 B per entry (FM @0, AM @256, SSB @512), 12 B used,
GB2312, empty = FF – the radio then shows **"Unknown"** on the display (the
text is in the firmware, not in memory; VERIFIED 2026-09-26).

**Image (`.img`, 33 152 B)** = the segments above concatenated in this
order (channels 0x0000, VFO 0x7800, functions 0x7900, DTMF 0x7A00,
FM/AM/SSB 0x7C00, names 0x7E00, APRS 0x8100) – byte-for-byte the same
format as the RT950Pro app's backups.
Decoding: `drivers/radtel/rt950pro_map.py` (port of `Model/*.swift`).

Areas not read: 0x7800–0x7FFF, 0xC000–0xCFFF, 0xD300–0xFFFF (0xE000+ is
calibration – NEVER write).

## Channel (32 B)

```
0-3 RX BCD (LSB first, unit 10 Hz)   4-7 TX BCD
8-9 RX tone  10-11 TX tone   (00 00 = off; 2nd byte 00 → DCS index from 1; else CTCSS ×0.1 Hz LE)
12 low: signalling group   13 low: PTT ID
14 low: power 0=High 1=Mid 2=Low, high: scrambler
15 b7 FHSS learning, b6 narrow, b5-4 encryption, b3 BCL, b2 scan, b1 TX enabled, b0 AM
16-19 FHSS (3 B + 0xA0); without FHSS the radio writes 00 00 00 00
20-31 name GB2312, padded with FF
```
Empty slot = 32 × FF. A CB channel created by the radio: byte 14 = 00, byte 15 = 06.

## Zones (VERIFIED)

**10 zones × 99 channels** (the last zone has 69 slots). Zone z = slots
(z−1)·99+1 … z·99. Verified by storing to "Zone 2, position 99" → slot 198.
(Wrong earlier guesses: 10×96, 15×64.)

The sister RT-920 (CHIRP `radtel_rt900.py`) has the same 10×99 and also:
- **zone names at 0xC800** (10 × 16 B: 10 characters + 6 unused),
- **Zone/Channel mode at 0x9028** (0 = zones, 1 = all channels).
**Unverified** for the RT-950 Pro – the first candidate when exploring 0xC000–0xCFFF.

## Channel visibility – Work Band (VERIFIED)

Global menu **Work Band**: 64–999 MHz (default) or 18–64 MHz. The radio
**does not show** channels outside the active band, and a zone without any
visible channel is missing from the menu. That is why the CB channels
(27 MHz) were invisible. "All Band Test Mode" (power on with the ▼ arrow):
18–64 MHz only in section C. The Work Band byte in memory is still unknown
(candidate: block 0xB000 byte 33).

## Known bugs of the references (do not repeat)

- The reference Python driver always reads the VFO power as High (wrong clamp).
- The reference driver writes "no FHSS" as FF FF FF FF (the radio writes 00).
- The reference driver overwrites empty FM/AM/SSB entries from FF to 00 on write.
- The reference driver and the RT950Pro app scale AM/SSB frequencies ×10 kHz
  (correct: 1 kHz).

## Sources

- https://github.com/nivingoonesekera/Radtel-RT-950Pro-BLE-bridge-for-CHIRP-and-CPS
- https://github.com/NathanBarguss/Chirp_Radtel-RT-950-Pro
- CHIRP `chirp/drivers/radtel_rt900.py` (RT-920 zones)
- Funkbasis.de thread "Radtel RT-950 Pro" (Work Band, CB zones)
