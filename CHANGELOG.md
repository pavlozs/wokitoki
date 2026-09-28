# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] – 2026-09-28

First public version.

### Added
- Transports: Bluetooth LE (bleak) and serial/USB cables (pyserial); `scan`
  lists BLE devices and cables and identifies the radio by its handshake.
- Aliases (`alias add/list/show/rename/rm/edit`), `ping`, `paths`, `radios`.
- `read` (YAML + byte backup), `decode` (also CHIRP `.img` files), `write`
  (patch → diff → confirm → backup → write → verify), `.img` restore.
- Drivers: Radtel RT-950 Pro (BLE), Radtel RT-900 (BLE, cable), Quansheng
  UV-K5 with F4HWN v4.x (cable) – channels, settings and FM memories writable.
- Channel libraries for the Czech Republic (PMR446, CB, shared VHF/UHF,
  amateur calling) and FM station libraries for all Czech regions generated
  from ČTÚ open data.
- Default configurations per country and radio (`profile list/show/apply/build`).
  Imported channels are receive only unless the library clearly allows TX
  (PMR446, amateur and receive-only libraries are RX only by default);
  `--tx off|on` and `--tx-on <library>` switch TX with a warning.

### Fixed (pre-release review)
- `write` with a YAML that lists only some zones no longer erases the zones
  left out.
- A setting sharing a byte that is not set in the radio no longer resets the
  other settings in that byte; the UV-K5 byte 0x1FF5 (value FF is real) is
  decoded correctly.
- FM memories without names or not sorted by number are accepted; UV-K5
  channels in USB mode and channels holding odd values can be written back.
- A UV-K5 channel rename changes only the 10 name bytes the firmware uses.
- A Radtel block reply from another address is retried, never stored.
- Clear errors instead of tracebacks: unplugged cable, BLE setup failure,
  short UV-K5 replies, broken `aliases.yaml`, bad profile numbers, missing
  files; `read -o x.img`, `decode -o <the image>` and `profile apply --save`
  over an existing file are refused; an alias from a YAML must be a valid name.
