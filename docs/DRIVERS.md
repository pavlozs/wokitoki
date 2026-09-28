# How to write a driver for a new radio

1. File `src/wokitoki/drivers/<vendor>/<model>.py`, a class derived from
   `wokitoki.core.driver.Driver` (interface in [DESIGN.md](DESIGN.md#driver-module-for-one-radio)).
2. Registration in `pyproject.toml` (built-in drivers are also listed in
   `core/driver.py: BUILTIN_DRIVERS`):
   ```toml
   [project.entry-points."wokitoki.drivers"]
   radtel-rt950pro = "wokitoki.drivers.radtel.rt950pro:RT950Pro"
   ```
   An external package (`wokitoki-baofeng` etc.) registers its drivers the same way.
3. Describe the radio in `docs/radios/<vendor>-<model>.md`: protocol, memory
   map, what is verified on hardware and what is only taken over.
4. Tests: decoding/encoding on a sample `.img` (round trip
   `encode(decode(img), img) == img`) and reading through the fake radio in
   `tests/fakes.py` – no radio needed. Personal backups do not go into the
   repository; tests look for them outside (e.g. `WOKITOKI_TEST_IMAGES`).

## Mandatory driver properties

- `encode()` **patches** the original image, it never builds memory from scratch.
- `capabilities` describes: number of channels, zone layout, RX/TX bands,
  power level names, max. name length + character set, supported tones,
  whether it supports zone names.
- `writable_ranges()`: image ranges `write_image` sends to the radio (never
  calibration or unknown areas); `check_write` enforces it before every write.
- `encode()` must raise `CodeplugError` for changes it cannot encode (never
  ignore them) – see `drivers/radtel/encoder.py`.
- Every claim about the format that is not verified on a radio is marked
  **unverified** in the documentation.

## Checklist before the first write to a radio

- [ ] reading twice in a row gives the same image (stability)
- [ ] decode/encode round trip is byte-identical
- [ ] changing one channel name changes only the bytes of that name (diff)
- [ ] a `.img` backup written back reads back identically
