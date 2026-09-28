# wokitoki – commands

`command subcommand` style (like git). Wherever a radio is expected you can
give an **alias** (`cb-handheld`) or directly `--driver … --ble ADDR` /
`--port COM3`. Commands not implemented yet are marked *(planned)*.

## Radios and aliases

```sh
wokitoki radios                       # supported radios (drivers) and what they can do
wokitoki scan                         # finds BLE + USB/COM devices, guesses the model
wokitoki scan --ble --timeout 10
wokitoki scan --save                  # asks for an alias name after the scan
wokitoki scan --serial --no-probe     # list cables without talking to them (default: identify the radio)

wokitoki alias add cb-handheld --driver radtel-rt950pro --ble A1B2C3D4-E5F6-4711-8899-AABBCCDDEEFF
wokitoki alias list                   # table: name, radio, connection, note
wokitoki alias show cb-handheld
wokitoki alias rename cb-handheld rt950
wokitoki alias rm rt950
wokitoki alias edit                   # opens aliases.yaml in $EDITOR
wokitoki ping rt950                   # connects, handshake, prints model/firmware
wokitoki ping --driver auto --ble ADDR   # which radio is it? tries each driver's handshake
```

## Read / write

```sh
wokitoki read rt950                           # → ~/wokitoki/rt950/rt950_2026-09-26_1012.yaml + .img
wokitoki read rt950 -o mine.yaml
wokitoki decode backup.img                    # .img → YAML without a radio (driver by file size, else --driver)
wokitoki write rt950 mine.yaml                # read radio → show changes → confirm → backup → write → verify
wokitoki write rt950 mine.yaml --dry-run      # only shows what would change (still reads the radio)
wokitoki write rt950 backup.img               # restore a backup (only the writable areas)
wokitoki write --driver radtel-rt900 --port /dev/cu.usbserial-110 mine.yaml   # without an alias
wokitoki diff mine.yaml rt950                 # (planned) compare a file with the radio
wokitoki diff old.yaml new.yaml               # (planned) compare two files
```

`write` never trusts the file blindly: it reads the radio, patches that
image with the YAML (only fields that differ), refuses changes outside the
driver's writable areas, prints every change (`~ zone 1 slot 17 UHF1: power
high → mid`), asks (`--yes` skips it), saves the radio's previous state as
`<alias>_<date>_before-write.img`, writes, reads the radio back and saves the
verified state as a new `<alias>_<date>.yaml` + `.img` (`--no-verify` skips
it). A YAML read from another alias needs `--force`. If writing breaks off,
the message tells how to restore the backup.

`read` stores the `.img` first (the most valuable part), then the YAML. Files
are never overwritten: the same minute → suffix `_2`; `-o` pointing to an
existing file needs `--force`. `decode` also reads CHIRP `.img` files (the
metadata at the end is cut off).

## Channel libraries

```sh
wokitoki lib list                                    # channel and FM station libraries, transmit rules
wokitoki lib show cz/cb-cept40                       # legal notes + channels with defaults applied
wokitoki lib apply cz/cb-cept40 mine.yaml --zone 2 --start 1 --power mid   # (planned)
wokitoki lib apply cz/pmr446 mine.yaml --zone 3 --replace                  # (planned)
```

**Transmitting on imported channels.** By default the profile and the
library's legal status decide: libraries marked `restricted` (PMR446 – 0.5 W
ERP and a fixed antenna, which these radios do not meet), `licence` (amateur
bands) and `rx-only` are imported **receive only**; profiles may switch off
more (the Czech profiles do it for CB – the radios are not type-approved).
`--tx off` makes everything receive only, `--tx-on <library>` (repeatable)
switches TX on for one library, `--tx on` for every library that allows it
at all. Receive-only libraries (e.g. airband) can never transmit. Every TX
switched on against these defaults prints a warning – it is **at your own
responsibility**. How "receive only" is stored depends on the radio (Radtel:
`duplex: off`, UV-K5: `extra: {tx_lock: true}`).

User libraries go to `<config>/library/<country>/<name>.yaml` (`wokitoki
paths`); one with the same id as a built-in library overrides it.

## Default configurations (profiles)

```sh
wokitoki profile list                              # profiles per country and radio
wokitoki profile show cz/radtel-rt950pro            # blocks, settings, FM library
wokitoki profile apply rt950                        # default profile cz/<driver>: diff → confirm → backup → write → verify
wokitoki profile apply rt950 --fm cz/fm-ostrava     # FM stations of your region
wokitoki profile apply uvk5 --merge --dry-run       # keep channels in slots the profile does not use
wokitoki profile apply uvk5 --save uvk5-cz.yaml     # also keep the result as YAML (existing file: --force)
wokitoki profile build backup.img -o cz.yaml --fm cz/fm-brno   # offline: review/edit, then `write`
wokitoki profile apply rt950 --tx off               # every imported channel receive only
wokitoki profile apply rt950 --tx-on cz/pmr446      # TX only for this library – at your own responsibility
wokitoki profile apply rt950 --tx on                # TX wherever the library allows it at all (warnings)
wokitoki lib list                                   # also lists the FM station libraries cz/fm-<city>
```

## Editing data *(planned)*

```sh
wokitoki export mine.yaml channels.csv --zone 2      # channels to CSV (Excel)
wokitoki import mine.yaml channels.csv --zone 2      # back (replaces the zone)
wokitoki check mine.yaml                             # validation: bands, name lengths, legality
wokitoki fmt mine.yaml                               # reformats / adds comments
```

## Paths

```sh
wokitoki paths                                # where aliases, libraries and data are
```

## Diagnostics *(planned)*

```sh
wokitoki probe rt950 --range 0xC000-0xCFFF    # READ ONLY of unknown areas → .bin
wokitoki hexdiff a.img b.img                  # differences of two backups by field/offset
wokitoki -v …                                 # verbose log (TX/RX in hex) – available
```

## Global options

`-v/--verbose`, `--log FILE`, `--plain` (no colours, for scripts),
`-y/--yes` *(planned)* (no confirmation – scripts only, a write also
requires `--force`).

## Open questions

- CSV `import`: replace the whole zone or merge by slot? (proposal: `--replace` / `--merge`)
- ~~Where does `read` store files?~~ Decided: `~/wokitoki/<alias>/`, `-o` for another place.
