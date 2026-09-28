# wokitoki

[![CI](https://github.com/pavlozs/wokitoki/actions/workflows/ci.yml/badge.svg)](https://github.com/pavlozs/wokitoki/actions/workflows/ci.yml)
[![License: GPL v3+](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)

A command line programmer for handheld radios. The radio's configuration is
read into readable **YAML**, edited in any text editor and written back – over
**Bluetooth LE** or a USB programming cable. Runs on macOS, Linux and Windows.
(The name is the Czech pronunciation of "walkie-talkie".)

```console
$ wokitoki scan --save                        # find the radio and give it a name
$ wokitoki read rt950                         # → ~/wokitoki/rt950/rt950_2026-09-26_1012.yaml (+ .img backup)
$ $EDITOR ~/wokitoki/rt950/rt950_2026-09-26_1012.yaml
$ wokitoki write rt950 ~/wokitoki/rt950/rt950_2026-09-26_1012.yaml
Changes to write to rt950 (RT-950):
  ~ zone 1 slot 17 UHF1: power high → mid
  ~ zone 1 slot 30 VHF5: rx 173.005 → 173.05, power high → low
Write these changes to the radio? [y/N]:
```

## Features

- **Text instead of a GUI** – one channel per line in YAML, comments with the
  allowed values next to every setting; hand edits are validated before
  anything is sent to the radio.
- **Safe writing** – the radio is read first, only fields that differ are
  patched into its memory image (unknown bytes and calibration are never
  touched), every change is listed and confirmed, a backup is stored and the
  result is verified by reading the radio back.
- **Bluetooth LE and USB cables** through one transport layer; `scan`
  identifies the radio behind a cable or a picked BLE device by its handshake.
- **Aliases** – scan once, then just `wokitoki read rt950`.
- **Channel and broadcast libraries** – PMR446, CB, shared VHF/UHF, amateur
  calling channels and FM stations per region, each with its legal framework.
- **Default configurations (profiles)** per country and radio:
  `wokitoki profile apply rt950 --fm cz/fm-praha`.
- **Drivers as plugins** – a new radio is a new module; external packages can
  add drivers through the `wokitoki.drivers` entry point group.

## Supported radios

| radio | connection | status |
|---|---|---|
| Radtel RT-950 Pro | Bluetooth LE | reading and writing verified on hardware |
| Radtel RT-900 (BT) | Bluetooth LE, USB cable | reading and writing verified on hardware |
| Quansheng UV-K5 with F4HWN firmware v4.x | USB cable | reading and writing verified on hardware |

Writable today: channels, settings and FM memories (where the radio has them).
VFO, DTMF and APRS are shown read only – changing them is refused, not ignored.
Details per radio: [docs/radios/](docs/radios/).

## Installation

Python 3.11 or newer:

```sh
pipx install git+https://github.com/pavlozs/wokitoki
# or, for development
git clone https://github.com/pavlozs/wokitoki && cd wokitoki
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

- **macOS:** the terminal app needs the Bluetooth permission (System Settings →
  Privacy & Security → Bluetooth); without it macOS stops the process.
- **Linux:** BLE through BlueZ; serial cables usually need your user in the
  `dialout` group.
- **Windows:** Windows 10 or newer for Bluetooth LE.

## Quick start

```sh
wokitoki radios                          # supported radios and what they can do
wokitoki scan --save                     # BLE devices and USB cables → alias
wokitoki ping rt950                      # handshake: model and firmware
wokitoki read rt950                      # YAML + byte backup (.img)
wokitoki write rt950 rt950_….yaml --dry-run
wokitoki write rt950 rt950_….yaml        # diff → confirm → backup → write → verify
wokitoki write rt950 backup.img          # restore a backup
wokitoki lib list                        # channel and FM station libraries
wokitoki profile show cz/radtel-rt950pro
wokitoki profile apply rt950 --fm cz/fm-ostrava
wokitoki profile apply rt950 --tx off         # everything receive only
```

All commands: [docs/CLI.md](docs/CLI.md) · file formats: [docs/FORMATS.md](docs/FORMATS.md).

## ⚠️ Safety and the law

- Writing a radio's memory can leave it misconfigured. wokitoki always keeps a
  backup and never writes calibration, but **use it at your own risk** – see
  the warranty disclaimer in the [license](LICENSE).
- **PMR446 is receive only by default.** PMR446 may be used without a licence
  only with at most 0.5 W ERP and a **fixed, non-removable antenna**. Radios
  like the RT-950 Pro, RT-900 or UV-K5 have a removable antenna and more
  power, so the default profiles import PMR channels with TX switched off.
  Switching TX on (`--tx-on cz/pmr446`, `--tx on`, or `tx: on` in your own
  profile) is **at your own responsibility** – wokitoki prints a warning.
- **You are responsible for what you transmit.** Many frequencies are
  receive-only for you (airband, CB on radios not type-approved for it,
  amateur bands without a licence), power limits apply (PMR446 0.5 W ERP,
  shared frequencies per channel). The libraries and profiles mark this, but
  they describe one country's rules and may be outdated – check your national
  regulator.
- The FM station lists are estimates from transmitter data (terrain ignored).

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) – architecture and decisions
- [docs/DRIVERS.md](docs/DRIVERS.md) – how to add a radio
- [docs/radios/](docs/radios/) – protocols and memory maps (what is verified)
- [docs/TODO.md](docs/TODO.md), [docs/PROGRESS.md](docs/PROGRESS.md) – plan and log
- [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md)

## Acknowledgements

wokitoki stands on the work of others – thank you:

- [CHIRP](https://chirpmyradio.com) and its drivers (Radtel RT-900 family) –
  protocol and memory map knowledge.
- [armel/uv-k5-chirp-driver](https://github.com/armel/uv-k5-chirp-driver) and
  the F4HWN / egzumer UV-K5 firmware projects – UV-K5 protocol and memory map.
- [nivingoonesekera/Radtel-RT-950Pro-BLE-bridge-for-CHIRP-and-CPS](https://github.com/nivingoonesekera/Radtel-RT-950Pro-BLE-bridge-for-CHIRP-and-CPS)
  and [NathanBarguss/Chirp_Radtel-RT-950-Pro](https://github.com/NathanBarguss/Chirp_Radtel-RT-950-Pro)
  – RT-950 Pro BLE unlock, clone protocol and memory map.
- Czech Telecommunication Office (ČTÚ) – open data "Rozhlasové vysílače" used
  to generate the FM station libraries (`tools/gen_fm_libraries.py`); ČTÚ gives
  no warranty for the data.

## License

wokitoki is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License, version 3 or (at your option) any
later version** – see [LICENSE](LICENSE). Drivers distributed as separate
plugins that load into wokitoki are covered by the same terms.
