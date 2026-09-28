# wokitoki – file formats

Everything a human reads or edits is **YAML** (UTF-8, `#` comments allowed
and preserved by the program when writing – ruamel.yaml round trip).
Channel tables can also be converted to **CSV**. Binary backups are `.img`.

## 1. Aliases – `aliases.yaml`


Location: the user configuration directory (`wokitoki alias edit` opens it,
`wokitoki paths` prints it).

```yaml
# My radios. The file can be edited by hand.
aliases:
  rt950:
    driver: radtel-rt950pro
    transport: ble
    address: A1B2C3D4-E5F6-4711-8899-AABBCCDDEEFF   # macOS: UUID, Linux/Windows: MAC
    name: "walkie-talkie"        # how the device advertises itself
    note: "blue RT-950 Pro, firmware V0.24"
    added: 2026-09-26
  rt900:
    driver: radtel-rt900
    transport: ble
    address: 4A1B…
  cable:
    driver: radtel-rt900
    transport: serial
    port: /dev/cu.usbserial-110   # Windows: COM3
```

Note: macOS does not expose BLE MAC addresses, only UUIDs assigned by the
Mac. An alias from a Mac therefore cannot be moved to another computer –
`scan` finds the radio again.

## 2. Radio configuration – `~/wokitoki/<alias>/<alias>_<date>.yaml` + `.img`

`read` stores two files: the YAML for editing and the `.img` (byte copy of
the memory) as a backup and as the **base for writing** (unknown bytes are
taken from it).

```yaml
wokitoki: 1                      # format version
radio:
  driver: radtel-rt950pro
  model: RT-950                  # what the radio reported
  alias: rt950
  read_at: 2026-09-26T10:12:03
  image: rt950_2026-09-26_1012.img   # base for writing (path relative to this YAML)

zones:
  - zone: 1
    name: "PMR"                  # only if the radio supports zone names
    channels:
      - {slot: 1, name: "PMR 01", rx: 446.00625, mode: FM, bw: narrow, power: low, scan: true}
      - {slot: 2, name: "PMR 02", rx: 446.01875, mode: FM, bw: narrow, power: low, scan: true}
  - zone: 2
    name: "CB"
    channels:
      - slot: 1                  # position in the zone
        name: "CB 01"
        rx: 26.965
        offset: -7.6             # or: tx: 27.5 / duplex: off
        mode: FM
        bw: narrow
        power: mid
        rx_tone: off             # 88.5 | D023N | off
        tx_tone: off
        scan: true
        extra: {scrambler: 2, fhss: "123456"}   # radio specific

vfo:
  A: {rx: 145.525, shift: "+", offset: 0.6, power: high, step: 12.5}

settings:                        # each setting has a comment with its allowed values
  sql: 4
  language: english
```

Rules:
- Frequencies in **MHz** as a number (`145.5`), tones `88.5` / `D023N` / `D754I` / `off`.
- Power is given as a **level name** (`high/mid/low`), the driver converts it.
- A channel missing from the YAML is **empty** (it gets deleted in the radio on write).
- Unknown keys = error (typos do not get lost silently).
- **Free text** (channel and station names, DTMF codes, APRS call sign and
  message, alias notes) is always in **double quotes**: `name: "PMR 01"`.
  Without them `123` would be a number, `yes`/`off` a boolean in other tools
  (YAML 1.1) and `A, B` / `Channel: 2` would break the line. Values from a
  fixed list (`FM`, `narrow`, `off`, `high`) need no quotes.
- **Empty text vs. no value** (YAML standard): `""` = empty text, `null`
  (also `~` or nothing) = no value. wokitoki writes `name: ""` for a name
  without text (always present, so it is clear where to type) and `null`
  only for a setting that is not set in the radio (byte `FF`, factory
  default). When reading, `name:`, `name: null` and `name: ""` mean the same;
  `name: 123` is converted to text.
- Omitted `rx_tone`/`tx_tone` = `off`, omitted `extra` = the driver's
  defaults. A `tx` equal to `rx` is not written; a difference up to 70 MHz is
  written as `offset` (MHz, signed), a larger one as `tx`.
- **What `write` does with the file:** a channel missing from a zone that is
  listed is deleted; a zone left out of `zones:` (or `zones: []`) and a
  whole missing `zones:` key leave those channels as they are; a missing section (`vfo`, `dtmf`, …) or setting key is left as in
  the radio. Settings marked read only (e.g. RT-900 `radio_mode`) and
  sections the driver cannot encode yet (currently VFO, DTMF, FM/AM/SSB,
  APRS, zone names) must stay unchanged – a change is an error, never ignored.
- `mode` is the modulation the radio supports (Radtel: `FM`/`AM`, UV-K5 also
  `USB`); the driver checks it. Odd values the radio holds itself (`power:
  "?3"`, `rx_tone: "?F000"`, `mode: MOD3`) may stay – only a changed field is
  validated.
- Several settings can share one byte (e.g. `work_mode_a/b/c`). When that
  byte is not set in the radio (`null` for all of them), setting one needs
  values for all of them – wokitoki never guesses the others.
- Channels are written as `{…}` lines (one channel = one line); settings
  carry a comment with a description and the allowed values.

## 3. Channel CSV (export/import) *(planned)*

Columns (first line is the header, separator `,` or `;` – auto-detected):

```
zone,slot,name,rx,tx,offset,mode,bw,power,rx_tone,tx_tone,scan
2,1,CB 01,26.965,,,FM,narrow,mid,off,off,yes
```

Import also understands **CHIRP CSV** (Location, Name, Frequency, Duplex,
Offset, Tone…) so data from RepeaterBook etc. can be taken over.

## 4. Channel libraries – `library/<country>/<name>.yaml`

Built into the package (`src/wokitoki/library/`), user libraries in the
user `library/` directory. They describe **channels independent of any
radio** + the legal framework.

```yaml
library: cz/cb-cept40
title: "CB – 40 CEPT channels (FM)"
country: CZ
source: "ČTÚ – general authorisation for CB (check the current version)"
legal:
  tx: allowed                    # allowed | restricted | licence | rx-only
  max_power_w: 4
  note: "FM max 4 W; approved equipment only."
defaults: {mode: FM, bw: narrow, power: mid}
channels:
  - {name: "CB 01", rx: 26.965}
  - {name: "CB 02", rx: 26.975}
```

Unknown channel keys are an error; `library:` must match the file location
(`cz/pmr446` ↔ `library/cz/pmr446.yaml`). `wokitoki lib list/show` read the
libraries; ### Broadcast station libraries – `kind: broadcast`

```yaml
library: cz/fm-ostrava
kind: broadcast
band: fm                         # fm | am
title: "FM broadcast – Ostrava"
location: {name: "Ostrava", lat: 49.8209, lon: 18.2625}
stations:                        # ordered from the strongest
  - {name: "Frekvence 1", freq: 91, program: "Frekvence 1", transmitter: "Ostrava", erp_kw: 70.8, distance_km: 6}
```

`name` is a short name (≤ 12 ASCII characters, what fits a radio display).
The Czech FM libraries (`cz/fm-<city>` for all 13 regional capitals and
Prague) are **generated** by `tools/gen_fm_libraries.py` from ČTÚ open data
"Rozhlasové vysílače": for each programme the strongest transmitter within
80 km, estimated from ERP / distance² (terrain ignored). Regenerate them when
the data changes; do not edit them by hand.

`lib apply` *(planned)* inserts the channels into the chosen zone from the
chosen position, fills in `defaults`, checks that the radio supports them
(band, power) and disables transmit for `rx-only` libraries.

## 5. Default configurations (profiles) – `profiles/<country>/<driver>.yaml`

A profile is what a radio should hold by default in one country. Built into
the package (`src/wokitoki/profiles/`), user profiles in `<config>/profiles/`
override them by id. Another country = another directory (`profiles/sk/…`),
since channels, power limits and licences differ.

```yaml
profile: cz/radtel-rt900
title: "Czech Republic – Radtel RT-900"
country: CZ
driver: radtel-rt900
notes: ["…"]                     # shown by `profile show`, written into the YAML
channels: replace                # replace: only the profile's channels; merge: keep other slots
blocks:                          # channel libraries placed from a slot
  - {start: 1, library: cz/pmr446, power: low}
  - {start: 21, library: cz/shared, power_by_limit: {5: mid, 1: low}}   # legal limit (W) → level
  - {start: 1, library: cz/pmr446, power: low, tx: off}                 # receive only (the default for PMR anyway)
  - {start: 41, library: cz/cb-cept40, power: low, tx: off, scan: false} # receive only
  - {zone: 3, start: 1, library: cz/cb-cept40}   # radios with zones
  - {start: 121, library: cz/amateur-calling, extra: {tx_lock: true}}   # radio-specific extras
settings: {sql: 4, scan_mode: carrier}           # only these settings change
broadcast:
  fm: {library: cz/fm-praha, names: true}        # fills the FM memories (strongest first, sorted by MHz)
```

`tx` of a block: `off` = receive only, `on` = transmit, left out = the
library decides (`legal.tx: restricted | licence | rx-only` → receive only,
`allowed` → transmit). `--tx off|on` and `--tx-on <library>` override it for
one run; `rx-only` libraries never transmit. The driver stores "receive
only" its own way (`Capabilities.rx_only_extra`).

Applying a profile never builds the memory from scratch: the radio is read,
the profile is applied on top of its decoded configuration (settings and
sections the profile does not mention stay), then the normal `write` steps
follow (diff, confirmation, backup, write, verification).
