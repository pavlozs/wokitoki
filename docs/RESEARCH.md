# Research – similar projects (2026-09-26)

Conclusion: a universal **CLI** programmer for analog handheld radios
**with Bluetooth, aliases and a text configuration** does not exist. The
closest are:

| project | what it does | what to take |
|---|---|---|
| [CHIRP](https://github.com/kk7ds/chirp) | GUI, hundreds of radios, drivers in Python; BLE only via `ble-serial`; RT-900/910/920 yes, RT-950 Pro no | **drivers as the source of truth about formats** (`radtel_rt900.py`), CSV format |
| [qdmr / dmrconf](https://dm3mat.darc.de/qdmr/) | GUI + CLI for DMR radios, codeplug in **YAML** | structure of a YAML codeplug, CLI read/write/verify |
| [dmrconfig](https://github.com/OpenRTX/dmrconfig/wiki) | CLI for DMR, text configuration | text format as the "source of truth" |
| [plugsmith](https://github.com/bigshotClay/plugsmith), [codeplugger](https://github.com/Chicago-Offline/codeplugger) | generating codeplugs from repeater databases | channel libraries / repeater import |
| [ble-serial](https://github.com/Jakeler/ble-serial) | BLE → virtual serial port | an alternative, but we talk BLE directly (bleak) |
| [RT-950 BLE bridge](https://github.com/nivingoonesekera/Radtel-RT-950Pro-BLE-bridge-for-CHIRP-and-CPS) | CHIRP module + BLE bridge for the RT-950 Pro | BLE unlock, RT-950 Pro clone protocol |
| [NathanBarguss RT-950 Pro](https://github.com/NathanBarguss/Chirp_Radtel-RT-950-Pro) | CHIRP driver + memory map documentation | memory map, settings |
| [RT-950/950Pro Editor](https://github.com/cruzerdlc/RT-950-950Pro-Editor) | GUI (Windows), zones, BLE; binaries only | confirms that zone blocks exist ("zones and supporting blocks") |

## Notes

- CHIRP `radtel_rt900.py` covers the whole family: RT-900 BT (999 channels),
  RT-910 (15 × 64), RT-920 (10 × 99 + zone names at 0xC800), BJ7800, GS-10B.
  The RT-950 Pro is closest to the RT-920 → look for zones the same way.
- CHIRP wiki "BLE Radios": RT-900 BT, RT-490, RT-920 work over BLE;
  "after an upload the radio restarts and BLE drops".
