# TODO

## Publishing on GitHub
- [x] license GPL-3.0-or-later (LICENSE, pyproject), README for GitHub, CONTRIBUTING, CHANGELOG
- [x] CI (ruff, pytest on macOS/Linux/Windows × Python 3.11–3.13, build), issue/PR templates
- [x] personal data removed from docs and tests (BLE addresses, location)
- [x] repository https://github.com/pavlozs/wokitoki in README/CONTRIBUTING and `[project.urls]`
- [x] first commit, public repository, CI green on macOS, Linux and Windows (Python 3.11–3.13)
- [ ] later: publish on PyPI (trusted publishing from a GitHub release)

## From the pre-release review (cleanup, no known bugs)
- [ ] move vendor-neutral code (`Setting`, `ImageMap`/`patch_image`, bit
      helpers) from `drivers/radtel` to `core`; one DCS table
- [ ] `RadtelCloneDriver` base class for RT-900 / RT-950 Pro (same bodies)
- [ ] cache `libraries()` (profile build parses all library files ~6×)
- [ ] tests: patch `asyncio.sleep` and fake silences (suite takes ~50 s)
- [ ] RT-900 verify: read only the writable areas
- [ ] `profile apply --save`: keep the profile notes (build does)

## 0 – agree on the design
- [ ] Commands in [CLI.md](CLI.md) (mainly the open questions at the end).
- [ ] Formats in [FORMATS.md](FORMATS.md) (YAML + CSV + .img).
- [ ] Move `aliases.yaml` from the platform config dir to `~/wokitoki/`
      (all three OSes the same, not hidden)? Aliases named `library` would
      then be forbidden.

## 1 – core
- [x] `pyproject.toml` dependencies: bleak, pyserial, typer, rich, ruamel.yaml, platformdirs
- [x] `core/transport`: BLE (bleak) + serial, one interface, TX/RX log
- [x] `core/aliases.py`: load/save (ruamel round trip), add/list/rm/rename/show
- [x] `core/driver.py`: ABC + registry (built-in + entry points)
- [x] CLI: `radios`, `scan`, `alias …`, `ping`, `paths`
- [x] verified on hardware (macOS): `scan --ble --save` and `ping` with the RT-950 Pro
- [x] `core/codeplug.py`: model + writing YAML
- [x] `core/codeplug.py`: reading YAML back + validation, `diff`
- [ ] `alias rename`: offer to move `~/wokitoki/<alias>/` (only warns now)

## 2 – RT-950 Pro (port from the macOS project)
- [x] BLE unlock, handshake, XOR (`drivers/radtel/common.py`, `ping`)
- [x] block reading (`read_image`) + `wokitoki read` (YAML + .img), `wokitoki decode`
- [x] `read` works on hardware (2026-09-26)
- [x] two reads in a row without touching the radio = identical .img (BLE, 2026-09-27)
- [x] block writing, APRS commit (`write_image`), `wokitoki write` (not yet on hardware)
- [x] first write on hardware – over BLE from wokitoki (2026-09-27), verified
- [ ] USB cable: verify magic and baud rate (driver is BLE only for now)
- [x] decode channels, zones, VFO, settings, DTMF, FM/AM/SSB, APRS
- [x] encode channels + settings (patch the original image); VFO/DTMF/FM-AM-SSB/APRS/zone names: change = error
- [ ] confirm on the radio display that the AM/SSB unit is 1 kHz (derived from data, see PROGRESS)
- [ ] SSB slots 13 (0.256 MHz) and 16 (45.143 MHz) look like garbage – what does the radio show?
- [ ] writing FM/AM/SSB names (0xD000, 16 B per entry, GB2312) – together with `write`
- [x] tests on the RT950Pro backups (`~/Documents/RT-950Pro_*.img`, elsewhere via `WOKITOKI_TEST_IMAGES`)
- [x] byte round trip decode → YAML → encode on all 12 backups
- [ ] `wokitoki diff` (offline, two files)
- [ ] encode VFO, DTMF, FM/AM/SSB (names!), APRS
- [ ] explore 0xC000–0xCFFF: zone names (0xC800?) and Zone/Channel mode (0x9028?)
- [ ] find the Work Band byte (pair of backups before/after switching)

## 3 – RT-900
- [x] clone protocol per CHIRP (0x40 blocks, fixed SEND frame → key `CO 7`, calibration without XOR)
- [x] decode channels, VFO A/B, settings, DTMF, hidden mode; `decode` also reads CHIRP `.img`
- [x] `ping` + `read` over BLE and USB verified (2026-09-26); USB reads stable, mapped areas = BLE read
- [x] cable: CH340 `1a86:7523` (generic chip, says nothing about the radio), 57600 Bd
- [x] `scan` probes cable ports (known USB-serial chips only) with a quick handshake; `--no-probe`
- [x] BLE: FF31 exists, unlock accepted without a reply
- [ ] test whether the RT-900 works without the FF31 unlock (optional)
- [ ] confirm the power order on the display (data fit 0 H / 1 M / 2 L)
- [ ] airband channels are stored as FM – does the radio receive them in AM?
- [ ] explore the unmapped 0xE000–0xEFFF area the radio writes by itself (never write it)
- [x] encode + write (`W` blocks, CHIRP `_ranges` without 0xD000, never 0xE000+)
- [x] first write on hardware over USB (corrected rt900.yaml, verified by read-back)
- [ ] write over BLE (needs a terminal with the Bluetooth permission – see PROGRESS)

## 3b – Quansheng UV-K5 (F4HWN v4.x)
- [x] protocol (frames, XOR, CRC), hello/read/write/reset; `scan` identifies it
- [x] decode/encode channels + main settings; VFO, FM, welcome text, build flags read only
- [x] reading verified on hardware, YAML round trip identical, dry-run write
- [x] first real write (name change + revert), verified by reading back
- [ ] encode VFO, FM memories, welcome text, DTMF, scan-list priorities
- [ ] other firmware (stock, egzumer, F4HWN v5 for UV-K5 V3 / K1)

## 3c – profiles and broadcast
- [x] broadcast libraries (`kind: broadcast`), FM libraries for all CZ regions from ČTÚ open data
- [x] FM memories: RT-950 Pro (16, with names), UV-K5 (20, no names) encoded
- [x] profiles `profiles/<country>/<driver>.yaml`, `profile list/show/apply/build`, CZ profiles for all three radios
- [x] CZ profile applied to a UV-K5 (regional FM + existing airband channels kept)
- [ ] personal channel sets (e.g. local airband) as user libraries usable in profiles
- [ ] RT-900 FM memories – not in CHIRP's map; find them (backups before/after storing a station)
- [ ] remember the user's region (e.g. `fm` in the alias) instead of `--fm` every time
- [x] identify the picked BLE device in `scan --save`; `ping --driver auto`
- [x] BLE test: RT-950 Pro and RT-900 – ping auto, reads, dry-run writes
- [ ] profiles for other countries (sk, de, …)

## 4 – data
- [ ] CSV `export`/`import` (+ CHIRP CSV)
- [x] `lib list`, `lib show` (core/library.py, user libraries override built-in ones)
- [ ] `lib apply` (needs reading YAML back) – with the same TX rules as profiles (`--tx`, `--tx-on`), `check` (bands, legality)
- [ ] Czech libraries: repeaters (ČRK) – where to get current data?
