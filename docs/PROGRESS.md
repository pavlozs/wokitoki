# Progress log

## 2026-09-28 – hardware check after the review fixes

- **RT-900 (VERIFIED):** BLE ping + read (970 blocks, no retries), dry run;
  USB ping + read (byte-identical to the BLE read), dry run, real write of a
  rename + verify, restore of the backup + verify. Only the radio's own
  programming log (0x8040–0x8FFF, 0xE000+) differs afterwards.
- **UV-K5 (VERIFIED):** the first real write failed – the review fix
  demanded replies of ≥ 8 B, but the write ACK is exactly 6 B (id, length,
  offset). The fake answered with 8 B, so tests did not see it. Fixed
  (`MIN_REPLY = 6`, fake sends 6 B like the radio). Then: rename write
  changed exactly the 6 name bytes (0xF50–0xF55), bytes 10–15 of the slot
  untouched; restore made the radio byte-identical to the read before the test.
- **RT-950 Pro over BLE (VERIFIED):** ping + read (259 blocks, no retries),
  then a real write of a YAML listing only zone 2 (renamed channel) with FM
  memories unsorted, without names, one new: 16 bytes, verified; zones 1 and
  3 kept, FM memories written. Restore of the backup: byte-identical to the
  read before the test.

## 2026-09-28 – fixes from the pre-release review

A review of the whole project (10 angles, findings checked against real
backups) found these bugs, all fixed with tests in `tests/test_safety.py`:

- **Zones left out of the YAML were erased** (`encoder._patch_channels`
  compared all zones of the radio with only the listed ones). Now only listed
  zones are compared; the UV-K5 `zones: []` leaves channels alone.
- **Settings sharing a byte:** `Setting.encode` reset a byte that was FF to 0,
  silently changing the other settings in it. Now the other bits are kept, and
  when the whole byte is unset all settings in it must be given (error
  otherwise). `Setting.ff_unset=False` for UV-K5 0x1FF5, where FF is a real
  value. The RT-950 Pro profile now sets `work_mode_c` too.
- **FM memories:** the generic "unsupported section changed" check compared
  the whole `broadcast` section after the FM encoder – entries without a name
  or unsorted were refused. `ImageMap.encoded_extra` lists what the driver
  encodes itself; the check skips it.
- **Modes:** core accepts any modulation name, the driver validates it
  (UV-K5 `USB` round-trips). `check_common` validates only changed fields, so
  `?3` / `?F000` values the radio holds do not block other edits.
- UV-K5 rename writes only the 10 name bytes (a new channel still gets 16).
- Radtel `read_block`: a reply for another address is retried (and an error
  after 3 tries), never stored at the wrong offset.
- Transports: serial `reset_input`/`flush` errors, BLE setup errors and
  OSError/EOFError on writes become `TransportError`; a failed BLE connect is
  cleaned up; a late disconnect callback of an old client is ignored; the
  `BufferedTransport` event is per read (it bound to the first event loop).
- Quansheng: replies shorter than 6 B are a `DriverError` (was IndexError).
- CLI: `read -o x.img`, `decode -o <image>`, `write missing.yaml`,
  `profile apply --save` over an existing file (new `--force`), `scan --save`
  choice `0`, alias from a YAML used as a folder (validated), `relpath`
  across Windows drives, `$EDITOR` with quotes on Windows, profile notes kept
  by `profile build`.
- Aliases: invalid YAML and unquoted numeric keys are clear `AliasError`s.
- Profiles: `zone`/`start`/`count` must be whole numbers (`count: 0` imported
  everything), `power_by_limit` must be a mapping.
- Not changed (noted in TODO): shared generic encoder code still lives in
  `drivers/radtel`, duplicated RT-900/RT-950 driver bodies, library YAML
  reloading, slow tests (real sleeps).

## 2026-09-28 – PMR446 receive only by default, global TX switches

- The maintainer pointed out that PMR446 needs ≤ 0.5 W ERP and a fixed antenna,
  which these radios do not meet – the default profiles must not transmit on
  PMR. Before, all three CZ profiles imported PMR with TX on and there was no
  global TX switch (only `tx: off` per profile block; the library's
  `legal.tx` was ignored).
- `core/profile.py`: `decide_tx()` – `--tx-on <library>` → global `--tx` →
  block `tx: on|off` → library `legal.tx` (`restricted`, `licence`, `rx-only`
  → receive only). `rx-only` libraries can never transmit (a global `--tx on`
  skips them, `--tx-on` of one is an error). Warnings "at your own
  responsibility" for TX on restricted/licensed libraries or against the
  profile. `Capabilities.rx_only_extra` tells how a radio stores receive only
  (UV-K5 `tx_lock`; the K5 profile no longer needs `extra: {tx_lock: true}`).
- CLI `profile apply/build --tx profile|off|on --tx-on LIB`; `profile show`
  shows RX only / TX per block.
- Profiles: PMR `tx: off` with a note; README/CLI/FORMATS/DESIGN explain it.
- The radios themselves were not changed (their PMR channels still transmit,
  as the maintainer wanted earlier). 171 tests.

## 2026-09-28 – ready for GitHub (GPL-3.0-or-later)

- License GPL-3.0-or-later: the official GPL v3 text in `LICENSE`,
  `license = "GPL-3.0-or-later"` + `license-files` in pyproject (PEP 639,
  hatchling ≥ 1.27). Reason in DESIGN.md (CHIRP/F4HWN sources are GPL).
- README rewritten for GitHub (features, radios, install, safety and law,
  acknowledgements, license); CONTRIBUTING.md, CHANGELOG.md (0.1.0);
  `.github/`: CI workflow (lint, tests on 3 OSes × 3 Pythons, build with a
  check that libraries/profiles are in the wheel), issue templates (bug, new
  radio), PR template; `.gitignore` extended; `.github` left out of the sdist.
- Personal data removed: the maintainer's BLE addresses in docs/tests replaced
  by made-up ones, a location inference in this log reworded.
- Version 0.1.0; the wheel installs into a clean venv and finds its drivers,
  18 libraries and 3 profiles. ruff ≥ 0.16 required (its default rule set is
  what the code is checked against).

## 2026-09-27 – real writes over BLE (both Radtels, VERIFIED)

- RT-950 Pro: "PMR 01" → "PMR 01 TEST" and back. Backup, 259 blocks (incl. the
  APRS commit) all ACKed in 29 s, reconnect + verification read OK at the
  first attempt; after the revert the radio is byte-identical with the read
  before the test.
- RT-900: "PMR1" → "PMR1 TEST" and back. 507 blocks all ACKed in 18 s, the link
  stayed up (no restart), verification OK; afterwards only the radio's own
  0xE000 log area differs from the state before the test.
- Every read/write path is now verified on hardware: RT-950 Pro BLE, RT-900 BLE
  and USB, UV-K5 USB.
- Found while checking the RT-900 afterwards: the radio appends a 66-byte
  record to 0x8040–0x8FFF (and a few bytes to 0xE000) on every programming
  session – seen for yesterday's USB write and both BLE writes, never for a
  read. Not written by wokitoki; documented in docs/radios/radtel-rt900.md.

## 2026-09-27 – BLE test of both Radtels

- RT-950 Pro: two reads in a row byte-identical (259 blocks, 30–40 s, no
  retry); since yesterday only the maintainer's own edits changed (UHF 5 Mid → Low –
  the audit fix done on the radio, FM 1, VFO A, broadcast mode); dry-run write
  of the fresh YAML → "Nothing to write".
- RT-900 over BLE for the first time from wokitoki: identified by
  `ping --driver auto` (RT-900 magic answered, ident `01 36 01 80 …`); it has
  FF31 and accepts the unlock but sends no reply; read 970 blocks in 33 s
  without a retry; mapped areas identical to the state written over USB
  (only the radio's 0xE000 log area differs); dry run → "Nothing to write".
- Test files: `~/wokitoki/rt950pro/ble-test-{1,2}.*`, `~/wokitoki/rt900/ble-test.*`
  and the `ble-*.log` next to them.

## 2026-09-27 – identifying the radio behind a BLE address

- Decision: no automatic probing of every BLE device (FFE0 = generic HM-10
  module in other people's gadgets, slow connects, radios busy with a phone).
  Instead `_identify(kind, target)` – each driver one quick read-only
  handshake – is used for the device the user picks in `scan --save` when the
  guess is uncertain, and by `ping --driver auto --ble/--port`.
- The RT-950 Pro and RT-900 both advertise FFE0 but answer only their own
  magic, so the handshake tells them apart.
- Verified `ping --driver auto` over the cable on the UV-K5 (0.3 s) and over
  BLE on the RT-950 Pro (7.7 s: RT-900 magic unanswered after 1 s, RT-950
  handshake OK). BLE works from a regular terminal (a process without the
  macOS Bluetooth permission is killed, exit 134). `ping --driver auto` now reuses the
  identifying handshake instead of connecting a third time; a failed quick
  probe logs at DEBUG, not WARNING. 152 tests.

## 2026-09-27 – CZ profile loaded into a UV-K5 (test radio)

- Built a configuration for the test radio (`uvk5-cz.yaml`):
  profile cz/quansheng-uvk5-f4hwn + `cz/fm-ostrava` (20 stations), the radio's
  own airband channels moved from 31–35 to 131–135 unchanged, and added
  136 LKFR Frýdlant RADIO 123.490 and 137 LKZA Zábřeh RADIO 123.605 (verified in
  the ŘLP VFR manual, aim.rlp.cz, 22 JAN 26).
- Dry run 147 changes / 3 388 bytes, written, verified by read-back; audit:
  120 channels, PMR/shared at nominal power + narrow, 83 CB/amateur RX-only.
- Idea for later: personal additions (airband) as user libraries + a user
  profile, so re-applying the default keeps them (needs per-channel extras in
  libraries, e.g. scan lists).

## 2026-09-27 – default configurations (profiles) and FM station libraries

- Reviewed the three radios' configurations; wrote Czech default profiles
  `profiles/cz/{radtel-rt950pro,radtel-rt900,quansheng-uvk5-f4hwn}.yaml`: PMR,
  shared VHF/UHF (power from the legal limit via `power_by_limit`), CB 1–80
  and amateur calling as receive only, a few CZ-relevant settings (squelch,
  carrier scan, no roger/PTT ID/DTMF, F-LOCK and SetPwr on the K5), FM.
- FM libraries: `tools/gen_fm_libraries.py` turns ČTÚ open data "Rozhlasové
  vysílače" (1 165 transmitters) into `library/cz/fm-<city>.yaml` for the 13
  regional capitals + Prague – strongest transmitter per programme within
  80 km (ERP / d²), short names ≤ 12 ASCII. The maintainer's FM memories (92.5, 91.0,
  92.1 MHz) match transmitters of one region – the libraries work as intended.
- `core/library.py`: `kind: broadcast` libraries; `lib list/show` show them.
- Encoders for FM memories: RT-950 Pro 16 × (10 kHz units + 12 B GB2312 name,
  empty = FF), UV-K5 20 × (100 kHz units, empty = FFFF, no names); `diff`
  shows numbered memories one per line.
- `core/profile.py` (load, `build()` on top of the decoded radio: replace or
  merge channels, settings, FM), CLI `profile list/show/apply/build`;
  `write` refactored into `_write_flow` shared with `profile apply`.
- UV-K5 dry run of the CZ profile with `--fm cz/fm-ostrava`: 3 157 bytes. Noted
  that the radio was restored to its 13:41 state at 13:48 (by hand).
- 149 tests (every profile builds and encodes for its radio; merge; errors;
  CLI apply/build with the fake UV-K5).

## 2026-09-27 – Quansheng UV-K5 with F4HWN v4.2

- Source: the F4HWN CHIRP driver 4.2.0 (armel/uv-k5-chirp-driver) – protocol,
  MEM_FORMAT, power/tone/step tables; downloaded for reading only.
- `drivers/quansheng/protocol.py`: frames `AB CD … DC BA`, XOR key, CRC16
  XMODEM, hello/read/write/reset over the generic serial transport (38400 Bd).
- `uvk5_map.py`: 200 channels (record + attribute byte + name in three
  places), 8 power levels, CTCSS/DCS index tables, scan lists, band attribute
  recomputed from the frequency, 52 settings incl. F4HWN ones; VFO slots, FM
  memories, welcome text, build flags read only. Encoding patches only
  changed fields; empty slot = FF record/name + attribute 0x0F.
- `uvk5.py`: driver `quansheng-uvk5-f4hwn`; refuses to write other firmware
  than `F4HWN v4.`; writes like CHIRP (0x0000–0x1CFF + 0x1FF2–0x1FFF) and
  resets the radio.
- On the maintainer's radio: hello `F4HWN v4.2`, read 8 KB in 4.5 s, two reads
  identical (next day only 0x0E7F differed – radio state), YAML round trip
  byte-identical, `scan` identifies it, dry-run write shows exactly one name
  change. Alias `uvk5` created. 139 tests (fake UV-K5 speaks the protocol).
- **First writes (VERIFIED):** channel 35 renamed "FIS" → "FIS TEST" and back;
  backup, 58 blocks, reset, read-back identical both times. The first
  verification read after the reset got 4 noise bytes (`98 DF 16 7F`) from
  the booting radio → `protocol._sync` now skips anything before `AB CD`
  (test added, 140 tests); the second write verified on the first attempt.

## 2026-09-26 – first real write: RT-900 over USB (VERIFIED)

- BLE needs the macOS Bluetooth permission – a process without it is killed
  (exit 134); BLE writes are run from a regular terminal with `--log`.
- USB: dry run showed exactly the 15 planned changes (48 bytes). Before the
  write a fresh read differed from earlier reads only in 0xE000–0xEFFF (81 bytes
  – the radio appends 16 B records at 0xEE40…; some log it keeps itself).
- `write … rt900.yaml --yes`: backup, 507 `W` blocks (channels 500, VFO 1,
  settings 1, DTMF 5), every block ACKed, no retries, 15 s writing (78 s incl.
  reads); read-back identical in all written areas. Audit of the new state:
  all 30 PMR/VHF/UHF channels match cz/pmr446 and cz/shared.
- Fix: writing via `--port` without an alias stored the backup in
  `~/wokitoki/radtel-rt900/`; it now uses the alias from the YAML (files moved
  to `~/wokitoki/rt900/`). Test added; 123 tests.

## 2026-09-26 – `write`

- `codeplug.load()` reads the YAML back with validation (unknown keys, tone
  syntax, 10 Hz raster, tx/offset/duplex, duplicate slots…); `codeplug.diff()`
  lists changes for humans.
- `drivers/radtel/encoder.py`: patches the *current* radio image – decode it,
  compare with the YAML channel by channel / setting by setting, rewrite only
  differing fields. New channels start from the radio's own template (RT-950
  Pro like a channel created on the radio: FHSS 00×4; RT-900 like CHIRP: 16×00
  + 16×FF); deleted channels = 32×FF. Sections without an encoder (VFO, DTMF,
  FM/AM/SSB, APRS, zone names, RT-900 `radio_mode`) → error when changed.
- Per radio: channel encoders mirroring the decoders, `Setting.encode`,
  `WriteArea`s (RT-950 Pro exactly like the RT950Pro app incl. APRS commit;
  RT-900 CHIRP `_ranges` without 0xD000), `write_areas()` with ACK per block,
  `Driver.check_write()` refusing bytes outside the writable ranges.
- CLI `wokitoki write <alias> <yaml|img>`: read radio → patch → check → diff →
  confirm → backup `_before-write.img` → write → read back, compare, save the
  verified state. `--dry-run`, `--yes`, `--force`, `--no-verify`; `.img` restore
  copies only the writable areas.
- Proof: all 13 real backups survive decode → YAML file → load → encode byte for
  byte; the maintainer's corrected `rt900.yaml` changes exactly 48 bytes (the planned
  fixes). 121 tests (fake radio accepts written blocks).
- Not yet on hardware – the cable was unplugged when the dry run was tried.

## 2026-09-26 – `scan` identifies the radio behind a cable

- A cable's VID:PID only names the USB-serial chip, so `scan` now probes
  ports with a known chip: each serial-capable driver tries one quick
  read-only handshake (`Driver.identify(quick=True)`, Radtel: `quick_spec` =
  1 attempt, 1 s timeout). The first answer wins → "RT-900 via CH340 cable,
  radtel-rt900 (identified)". Other ports are never opened; `--no-probe`
  turns it off. Verified on the maintainer's RT-900: under 1 s.
- Tests: identified radio, silent cable stays "?", `--no-probe` never opens
  the port; 87 tests.

## 2026-09-26 – RT-900 over the USB cable (VERIFIED)

- The maintainer connected the RT-900 with a CH340 cable (`1a86:7523`,
  `/dev/cu.usbserial-11240`). No code change was needed: `ping` and `read`
  worked on the first try (57600 Bd, handshake identical to BLE, 970 blocks
  in ~30 s, no retries).
- Two USB reads are byte-identical (stable). Compared with the maintainer's BLE
  read from 21:03 all mapped areas are identical, decoded YAML too; only 17
  bytes in the unmapped 0xE000–0xEFFF area differ – state the radio writes by
  itself. → Never write that area.
- ~~The RT-900 profile lists the CH340 cable~~ – reverted right after: the
  user pointed out that `1a86:7523` identifies the USB-serial chip, not the
  radio (the same cable programs other radios). Known cable chips are now a
  core table (`USB_SERIAL_CHIPS`); `scan` shows "USB-serial cable (CH340)"
  without a driver guess and suggests `ping --driver … --port …`; 84 tests.
- Observations in the data: airband channels stored as FM (AM bit off),
  power values consistent with CHIRP's code order, two radio-created
  channels in slots 255/256. Details in docs/radios/radtel-rt900.md.

## 2026-09-26 – `lib list`, `lib show`

- The maintainer tried `wokitoki lib list` from the docs and got "No such command".
  The README example did not say the command was only planned.
- Implemented the read-only part: `core/library.py` (loads built-in and user
  libraries, user overrides built-in by id, validates keys, `legal.tx`,
  `library:` vs. file location, applies `defaults`), `wokitoki lib list`
  and `wokitoki lib show <id>`. `lib apply` stays planned (needs YAML read-back).
- README now marks planned commands; 83 tests.

## 2026-09-26 – everything in English

- All documentation, comments, CLI help and output, error and log messages,
  YAML comments and channel-library metadata translated to English (the
  maintainers keep talking Czech; the repository is English). Rule written
  into DESIGN.md and CONTRIBUTING.md.
- Setting labels (YAML comments) are English now; settings keys unchanged.
- The maintainer's two RT-950 Pro reads (20:48, 21:06) differ in 20 bytes of the
  VFO and FM/AM parameter blocks – the radio was used in between, so this is
  not yet the "two identical reads" stability check.

## 2026-09-26 – quotes around text, `null` vs. `""`

- Free text in YAML (names, DTMF codes, APRS call sign/message, alias notes)
  is always written in double quotes (`codeplug.text()`), so `123`, `yes`
  or `A, B` stay text. An empty name is `""`.
- `None` is written explicitly as `null` (ruamel wrote it in block style as an
  empty value `sql:`). `null` = not set in the radio (FF).

## 2026-09-26 – FM/AM/SSB: names and units

- The maintainer could not find the names of stored FM/AM/SSB stations in the
  YAML: in their radio all of them are empty (FF) and the YAML omitted empty
  names. `name` is now always present (also `""`) – for broadcast and for
  channels.
- AM/SSB frequencies are in **1 kHz** units, not 10 kHz like FM: values from
  the radio (14150, 9996, 17720, 9770 / SSB 7144) match the shortwave
  broadcast bands and 40 m. Previously the nonsense "AM 141.5 MHz" came out.
  The same bug is in RT950Pro (Swift `ModulationSettings.scaled`). The maintainer's
  latest YAML was regenerated from the .img.
- VERIFIED: the radio shows "Unknown" for all stored FM/AM/SSB stations –
  that is the firmware's placeholder for an empty name (FF); the string
  "Unknown" is in none of the 11 backups. Confirms the location of the names
  (0xD000).

## 2026-09-26 – Radtel RT-900 driver (read only, not verified on hardware)

- Source: CHIRP `radtel_rt900.py` (RT900BT, FW V1.20P), `mml_jc8810.py`
  (handshake), `baofeng_uv17Pro.py` (XOR) – downloaded for reading only,
  nothing copied except constants and the memory map.
- Shared Radtel protocol generalised (`HandshakeSpec`: optional `M`, fixed
  SEND frame, known idents only warn; `Segment.plain_from` for unencrypted
  calibration). CHIRP's fixed frame yields key `CO 7` in our key computation
  = exactly CHIRP's `_crypt(1, …)` – confirms it is the same scheme as the
  RT-950 Pro.
- Shared codecs moved to `drivers/radtel/codecs.py` (BCD, text, DCS table,
  `Setting` – new kinds `inverted_bool` and `map`).
- `rt900_map.py`: channels (tones, power and flags differ from the RT-950
  Pro), VFO A/B, 41 settings, DTMF, hidden mode at 0xD000. Image = the whole
  address space like CHIRP (62 080 B) → `wokitoki decode` also reads CHIRP
  `.img` files (cuts off the metadata).
- Documentation fix: the power order in CHIRP's code is 0 High, 1 Mid,
  2 Low (the earlier text took over a wrong comment), flag bit 1 = AM.
- `scan`: when several drivers get the same score (RT-950 Pro and RT-900
  both advertise FFE0) it shows both and `--save` asks for the driver
  without a default.
- Tests (76): tones, channels, VFO, settings, reading through a fake RT-900
  (no `M`, 970 blocks, calibration without XOR), an RT-950 Pro does not
  answer the RT-900 magic.

## 2026-09-26 – reading the complete configuration (`read`, `decode`)

- Radtel `common`: `Segment`, `read_block` (4 B header + 128 B, XOR, 3
  attempts on a complete dropout, warning on a mismatching header),
  `read_segments` (handshake right before the transfer, once more if the
  first block gets no answer, `E`).
- RT-950 Pro: `read_image` (7 segments, 259 blocks) → `.img` identical in
  layout to the Swift app's backups; `decode` (`rt950pro_map.py`): channels
  by zones 10 × 99, VFO A/B/C, 60 settings with labels, DTMF, FM/AM/SSB, APRS.
- `core/codeplug.py`: generic model (Channel, Zone, Section, Codeplug) and
  YAML writing per FORMATS.md (channel = one line, signed offset, comments).
- CLI: `wokitoki read <alias> [-o]` (progress, .img first, then YAML, never
  overwrites), `wokitoki decode <img>` (driver by size).
- Verified offline: all 10 backups from `~/Documents/RT-950Pro_*.img` decode
  and read back byte-identically through the fake radio (with XOR). 62 tests.
- Not verified then: AM/SSB frequency scale (fixed later), `read` on hardware.

## 2026-09-26 – core: transport, aliases, scan/ping

- `core/transport`: `Transport` (async byte pipe, TX/RX log at DEBUG),
  `BleTransport` (bleak 3, profile from the driver: UUIDs, unlock, 20 B
  chunks, flow control on macOS), `SerialTransport` (pyserial in a thread),
  `scan_ble`, `list_serial_ports`.
- `core/driver.py`: `Driver` ABC, `RadioInfo`, `Capabilities`, registry
  (built-in + entry points, a broken plugin only warns), `match_ble` by the
  FFE0 service and the name.
- `core/aliases.py`: `AliasStore` over `aliases.yaml` (ruamel round trip,
  comments and order preserved, atomic write via `.tmp`), validation of
  names and keys (typo = error).
- `drivers/radtel/common.py`: handshake (magic, `F` ident, `M` model, `SEND`
  frame → XOR key, 3 attempts, removal of a duplicate ACK), `xor_crypt`, `E`.
  `RT950Pro.identify` = handshake + `E`.
- **Fix during the port:** the `SEND` frame is 25 B – Swift allocates 25
  zeros and fills only 24, the last byte is `00`. Found by a test.
- CLI: `radios`, `paths`, `scan [--ble|--serial] [--all] [--save]`,
  `alias add/list/show/rename/rm/edit`, `ping`; `-v`, `--plain`, `--log`.
- Tests (44): aliases, XOR/frame, handshake against a fake radio
  (`tests/fakes.py`: retry, duplicate ACK), registry, CLI via CliRunner.
- **First hardware test (macOS):** the BLE scan found the RT-950 Pro, but
  `scan --save` crashed – bleak returns the address as `pyobjc_unicode` (a
  `str` subclass) which ruamel.yaml cannot write. Fixed by converting to a
  plain `str` (scan and alias) + test; `save()` cleans up `aliases.yaml.tmp`
  after an error.
- **VERIFIED on hardware (macOS, 2026-09-26):** `scan --ble --save` and
  `ping` with the RT-950 Pro – BLE transport, FF31 unlock, handshake
  (including the 25 B `SEND` frame) and `E` work.
- Before that: a BLE scan started from a process without the Bluetooth
  permission was killed by macOS TCC (exit 134); serial ports worked.

## 2026-09-26 – project start

- Name **wokitoki** (Czech pronunciation of "walkie-talkie", free on PyPI).
- The sister project `../RT950Pro` (macOS SwiftUI) stays; wokitoki is a
  cross-platform Python CLI. Both projects will be maintained.
- Research ([RESEARCH.md](RESEARCH.md)): a universal CLI with BLE and aliases
  does not exist; model: qdmr/dmrconf (YAML codeplug), CHIRP as the source
  of formats.
- CHIRP `radtel_rt900.py` brought: the RT-900 protocol (magic
  `PROGRAMBT80U`, 0x40 blocks), a hidden "radio mode" at 0xD000, and for the
  sister RT-920 zone names at 0xC800 + Zone/Channel mode at 0x9028 –
  candidates for the RT-950 Pro too.
- Design: [DESIGN.md](DESIGN.md), [CLI.md](CLI.md), [FORMATS.md](FORMATS.md),
  [DRIVERS.md](DRIVERS.md), radio descriptions in `radios/`.
- Czech channel libraries: CB 1–40, CB 41–80, shared VHF/UHF frequencies
  (VO-R/16), PMR446, amateur calling frequencies. No LPD433 yet (TODO).
- Decided: radio data in `~/wokitoki/<alias>/` (Windows
  `C:\Users\<name>\wokitoki\<alias>\`), configuration via platformdirs.
  Cross-platform rules in DESIGN.md (UTF-8, alias characters, relative paths).
