# Contributing to wokitoki

Thank you for helping! Bug reports with a log, new radio drivers, channel
libraries for other countries and documentation fixes are all welcome.

## Development setup

```sh
git clone https://github.com/pavlozs/wokitoki && cd wokitoki
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                      # no radio needed – fake radios speak the protocols
ruff check src tests tools && ruff format --check src tests tools
```

## Ground rules

- **Language:** everything in the repository is English – code, comments,
  documentation, CLI output, error and log messages.
- **Never break a radio:** a driver's `encode()` patches the original image
  and changes only fields it understands; unknown bytes and calibration are
  never written (`writable_ranges()`, `check_write()`). A change the driver
  cannot encode must raise `CodeplugError`, never be ignored.
- **Mark what is verified.** Every statement about a protocol or memory map
  in `docs/radios/` says whether it was tried on hardware or taken from a
  reference.
- **No personal data in the repository:** no backups (`.img`) of your radio,
  no BLE addresses, call signs or locations. Tests that use real backups look
  for them outside the repository and skip when they are missing.
- Files are opened with `encoding="utf-8"`, paths go through `pathlib`
  (wokitoki runs on macOS, Linux and Windows).
- Update `docs/PROGRESS.md` and `docs/TODO.md` with larger changes.

## Adding a radio

See [docs/DRIVERS.md](docs/DRIVERS.md): one module per radio, a description
in `docs/radios/`, tests with a fake radio (`tests/fakes.py`) and the
checklist before the first write to real hardware. A driver can also live in
its own package and register through the `wokitoki.drivers` entry point
group.

## Reporting a problem

Run the failing command with a log and attach it (it contains the raw
TX/RX bytes – remove anything private first):

```sh
wokitoki -v --log wokitoki.log read myradio
```

Please include the radio model, firmware version, connection (BLE / cable)
and operating system.

## License

By contributing you agree that your contribution is licensed under the
project's license, GPL-3.0-or-later.
