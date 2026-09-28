# wokitoki – design

A cross-platform (macOS / Linux / Windows) command line tool that reads and
writes the configuration of handheld radios. The configuration is edited in
**text files** (YAML, CSV), not in a GUI. First version:
**Radtel RT-950 Pro** and **Radtel RT-900** over **Bluetooth LE**.

This document is the project's "working memory" – update it before any
larger change. The change log is in [PROGRESS.md](PROGRESS.md), the plan in
[TODO.md](TODO.md).

## Goals

1. **One core, radios as modules (drivers).** Adding a radio = adding a
   driver, the core does not change. Drivers can also ship as separate
   packages (Python entry points), not only inside this repository.
2. **Aliases.** "Sniff" a radio once (`wokitoki scan`), save it under a name
   (`cb-handheld`) and use only the alias afterwards: `wokitoki read cb-handheld`.
3. **Everything is text and editable by hand.** Aliases, radio
   configuration and channel libraries are YAML with comments; channels can
   also be exported/imported as CSV (Excel, LibreOffice).
4. **Safety over convenience.** Every read also stores a byte backup
   (`.img`), a write always shows the difference and asks for confirmation,
   unknown bytes are never changed.
5. **Channel libraries** (Czech Republic: PMR446, CB, LPD433, amateur calling
   frequencies…) that can be inserted into a chosen zone of a particular radio.

## Layers

```
            ┌──────────────────────────────────────────────┐
  CLI       │ wokitoki (Typer): scan, alias, read, write,   │
            │ diff, export, import, lib, radios, …          │
            └───────────────┬──────────────────────────────┘
            ┌───────────────▼──────────────────────────────┐
  core      │ aliases · codeplug (model) · library ·       │
            │ diff · backup · driver registry · YAML/CSV   │
            └──────┬─────────────────────────┬─────────────┘
            ┌──────▼──────────┐     ┌────────▼─────────────┐
  drivers   │ radtel.rt950pro │ ... │ radtel.rt900         │
            │  (decoding,     │     │                      │
            │   encoding,     │     │                      │
            │   clone proto.) │     │                      │
            └──────┬──────────┘     └────────┬─────────────┘
            ┌──────▼─────────────────────────▼─────────────┐
 transport  │ BLE (bleak) · serial port/USB (pyserial)      │
            │ one "byte pipe" interface                     │
            └───────────────────────────────────────────────┘
```

### Transport

A single "byte pipe" interface – the driver does not know whether it talks
over BLE, a USB cable or a COM port:

```python
class Transport(Protocol):
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def write(self, data: bytes, *, confirm: bool = False) -> None: ...
    async def read_exact(self, n: int, timeout: float) -> bytes: ...
    def reset_input(self) -> None: ...
```

- **BLE** (`bleak`, runs on macOS/Linux/Windows): notify/write
  characteristic, 20 B chunks, flow control. Radio specifics (UUIDs, the
  FF31 unlock sequence of the RT-950 Pro) come from the **driver** as a
  `BleProfile`.
- **Serial** (`pyserial`): USB cable / COM port, baud rate from the driver.
- The code is `asyncio` (bleak is asynchronous); the CLI calls `asyncio.run`.
- BLE order (verified on the RT-950 Pro): notify on the data characteristic
  → unlock write → `settle` pause → replies to the unlock (prefix
  `reply_prefix`) are dropped until the first data write.
- bleak does no flow control for write-without-response on macOS, so the
  transport waits for `canSendWriteWithoutResponse` before every 20 B chunk
  (otherwise CoreBluetooth silently drops packets – learned in RT950Pro).
- **macOS permission:** BLE needs the Bluetooth permission for the app the
  terminal runs in (System Settings → Privacy & Security → Bluetooth).
  Without it the OS kills the process right away (SIGABRT, exit 134) – this
  cannot be caught in Python.
- Radios of one family advertise the same way over BLE (FFE0); when several
  drivers get the same score, `scan` shows all candidates and the user /
  `ping` decides.
- A USB programming cable is only a USB-serial chip (CH340, CP210x, FTDI,
  PL2303 – `USB_SERIAL_CHIPS`); its VID:PID says nothing about the radio and
  the same cable programs many radios. So `scan` **probes** ports with a
  known cable chip: every serial-capable driver gets one short read-only
  handshake (`identify(quick=True)`: 1 attempt, 1 s); the first radio that
  answers is shown as "identified". Ports without a known chip are never
  touched; `--no-probe` disables probing (e.g. an Arduino on a CH340 resets
  when the port is opened). `SerialProfile.usb_ids` is only for radios with their own USB interface.
- Identifying by handshake (`_identify`: every driver gets one short,
  read-only attempt) is done only on devices that are certainly the user's:
  known cable chips during `scan`, the device picked in `scan --save` when
  the guess is uncertain, and `ping --driver auto`. It is never done on every
  BLE device around – FFE0 is a generic HM-10 module found in other people's
  gadgets, and a scan must not send them unlock or programming bytes.
- `scan` hides BLE devices no driver matches and serial ports without a USB
  VID (Bluetooth-Incoming-Port, debug-console…); `--all` shows everything.

### Driver (module for one radio)

```python
class Driver(ABC):
    id: str                  # "radtel-rt950pro" – used in aliases
    vendor: str; model: str
    transports: set[str]     # {"ble", "serial"}
    ble: BleProfile | None   # service UUID, characteristics, unlock, names for scan
    serial: SerialProfile | None

    def match_ble(self, advert) -> int        # 0–100: how much the device looks like this radio
    async def identify(self, t) -> RadioInfo   # handshake, model, firmware
    async def read_image(self, t, progress) -> bytes
    async def write_image(self, t, image: bytes, progress) -> None
    def decode(self, image: bytes) -> Codeplug
    def encode(self, cp: Codeplug, base: bytes) -> bytes   # patches base, keeps unknown bytes
    capabilities: Capabilities                # zones, bands, power levels, tones, name length…
```

- Registration through the `wokitoki.drivers` entry point group; built-in
  drivers live in `wokitoki/drivers/<vendor>/<model>.py` and are also listed
  in `core/driver.py: BUILTIN_DRIVERS` (they work without installing the
  package).
- `transports` is derived from the `ble` / `serial` profile. `identify`,
  `read_image`, `write_image` default to `NotImplementedError` so drivers
  can be ported step by step; `wokitoki radios` shows what a driver can do.
- **`encode` always patches the original image** (`base`) – it changes only
  the fields it knows, and only those whose value differs from the decoded
  base (`drivers/radtel/encoder.py`: decode base → compare channel by channel,
  setting by setting). Odd values the radio wrote itself therefore survive,
  and `encode(load(save(decode(img))), img) == img` holds for every real backup
  (tested). `check_write` refuses any changed byte outside `writable_ranges()`. Lesson from the RT-950 Pro: unmapped bytes (and other
  firmware versions) must pass through unchanged.
- Code shared by a family (Radtel: handshake, XOR, 0x52/0x57 blocks) lives in
  `drivers/radtel/common.py`, byte codecs in `drivers/radtel/codecs.py`.

### Codeplug (radio-independent model)

```
Codeplug
  radio: {driver, model, firmware, read_at}
  zones:    [Zone]         # {number, name, channels}; slot = position in the zone
  vfo:      {A, B, C…}
  settings: {key: value}   # driver specific, described by the driver's tables
  extra:    {…}            # FM/AM/SSB, DTMF, APRS … driver specific
```

`Channel` has common fields (name, rx, tx/duplex/offset, mode FM/AM,
bandwidth, tones, power as a **level name** high/mid/low, scan, …) +
`extra` for radio-specific things (scrambler, FHSS…).

### Libraries and profiles

- **Channel libraries** (`library/<country>/*.yaml`) describe channels
  independent of any radio + the legal framework; **broadcast libraries**
  (`kind: broadcast`) list FM stations per region (generated from ČTÚ data).
- **Profiles** (`profiles/<country>/<driver>.yaml`) are default
  configurations: which libraries go where, with which power level
  (`power_by_limit` maps a legal limit to the radio's level), settings and the
  FM library. They are country specific on purpose – limits and licences
  differ per country. `core/profile.py: build()` applies a profile on top of
  the decoded radio; writing reuses the `write` flow (`cli._write_flow`).
- **TX of imported channels is off unless it is clearly allowed:** libraries
  marked `restricted` (PMR446: 0.5 W ERP + fixed antenna – typical handhelds
  do not qualify), `licence` or `rx-only` are imported receive only;
  `--tx`/`--tx-on` switch TX on with a warning ("at your own responsibility"),
  never for `rx-only`. Drivers say how they store "receive only"
  (`Capabilities.rx_only_extra`: Radtel duplex off, UV-K5 `tx_lock`).
- Drivers declare `Capabilities.fm_slots` / `fm_name_length` /
  `power_watts` so profiles and checks know what a radio can hold.

### Aliases

`wokitoki scan` finds devices (BLE, USB/serial), asks the drivers what they
are (`match_ble`) and offers to save an alias. Alias = name → {driver,
transport, address, note}. Format details in [FORMATS.md](FORMATS.md).

### Where things live (decided 2026-09-26)

| | path | Windows |
|---|---|---|
| configuration, `aliases.yaml`, user libraries | platformdirs `user_config_dir("wokitoki")` | `%LOCALAPPDATA%\wokitoki\` |
| **radio data** (read/write, backups) | **`~/wokitoki/<alias>/`** | `C:\Users\<name>\wokitoki\<alias>\` |

- `~` is never built by hand in code: `Path.home() / "wokitoki" / alias`.
- Overridable with `WOKITOKI_HOME` (data root) and `WOKITOKI_CONFIG`
  (configuration); `wokitoki paths` prints the paths actually used.
- Open question: move `aliases.yaml` to `~/wokitoki/` as well (the macOS
  location is hidden and contains a space) – see TODO.md.

### Cross-platform rules

1. Always open files **explicitly with `encoding="utf-8"`** (Windows would
   otherwise use cp1250 → broken Czech names).
2. An **alias** may contain only `a–z 0–9 - _` (it is part of a path;
   Windows forbids `: \ / * ? " < > |`).
3. Timestamps in file names without colons: `2026-09-26_1012`.
4. Links between files (YAML → `.img`) are always **relative** to the YAML file.
5. Aliases are not portable between computers (macOS only knows BLE UUIDs,
   ports are called `COM3` / `/dev/cu.usbserial-…` / `/dev/ttyUSB0`).
6. BLE on Windows needs Windows 10+ (bleak/WinRT).

## Safety rules (lessons from the RT-950 Pro)

1. **Every read** stores a byte backup `.img` next to the YAML.
2. **Write:** first a fresh read (current state of the radio) → build the
   image by patching → **diff** against the radio → confirmation → automatic
   backup → write → (optionally) verification read.
3. Writing a configuration read from a **different radio** (another alias /
   MAC) only with `--force`.
4. Never write areas the driver does not mark as safe (calibration,
   unknown areas).
5. A channel outside the active band / the radio's capabilities → warning
   (the RT-950 Pro hides channels outside the "Work Band", see
   radios/radtel-rt950pro.md).

## Decisions and why

- **Python + bleak**: the only reasonable cross-platform BLE library; the
  reference RT-950 driver and CHIRP are Python too (easy comparison).
- **YAML (ruamel.yaml)** for everything a human edits: it supports comments
  and ruamel **keeps** them when writing (the program can update an alias
  file the user annotated by hand). JSON has no comments, TOML is weaker for
  long channel lists. Precedent: qdmr/dmrconf stores the codeplug in YAML.
- **CSV** only as an exchange format for spreadsheet editing of channels
  (and importing CHIRP CSV / RepeaterBook).
- **Typer** for the CLI (type annotations → help, shell completion).
- **Rich** for tables and progress (`--plain` for scripts: tables as
  tab-separated lines).
- **License: GPL-3.0-or-later.** The protocol and memory map knowledge comes
  largely from CHIRP and the F4HWN CHIRP driver (both GPL); a GPL license keeps
  the door open to reuse their code directly, matches the ham radio software
  ecosystem (CHIRP, qdmr) and keeps forks of this tool free. External driver
  plugins load into the process and are covered by the same terms.
- **Language:** everything in the repository is English – code, comments,
  documentation, CLI output, error and log messages, YAML comments. (The
  maintainers talk Czech; Czech appears only in data such as channel names.)
