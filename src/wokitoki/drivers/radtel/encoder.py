"""Patch a memory image with a Codeplug – the heart of ``write``.

The image is never rebuilt: we decode the base image, compare it with the
wanted Codeplug and rewrite only the fields that differ. Unknown bytes, odd
values the radio produced itself and everything we cannot encode yet pass
through unchanged. A change in a section we cannot encode is an error, never
silently ignored.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from wokitoki.core.codeplug import Channel, Codeplug, CodeplugError, plain

from .codecs import Setting

CHANNEL_SIZE = 32


@dataclass(frozen=True)
class ImageMap:
    decode: Callable[[bytes], Codeplug]
    # Patch a 32 B record in place: old = the decoded channel (None for a new one).
    encode_channel: Callable[[bytearray, Channel | None, Channel], None]
    new_record: bytes  # template for a channel in an empty slot
    settings: tuple[Setting, ...]
    settings_offset: int
    settings_length: int
    channels_offset: int = 0
    # Parts of extra sections the driver encodes itself (e.g. "broadcast.fm");
    # the generic "unsupported section changed" check skips them.
    encoded_extra: tuple[str, ...] = ()


def patch_image(base: bytes, cp: Codeplug, spec: ImageMap) -> bytes:
    out = bytearray(base)
    current = spec.decode(base)
    if cp.zones_given:
        _patch_channels(out, current, cp, spec)
    _patch_settings(out, current, cp, spec)
    _check_unsupported(current, cp, spec.encoded_extra)
    return bytes(out)


def _patch_channels(out: bytearray, current: Codeplug, cp: Codeplug, spec: ImageMap) -> None:
    zones = {z.number: z for z in current.zones}
    wanted: dict[tuple[int, int], Channel] = {}
    for zone in cp.zones:
        if zone.number not in zones:
            raise CodeplugError(
                f"zone {zone.number} does not exist (the radio has zones {min(zones)}–{max(zones)})"
            )
        size = zones[zone.number].size
        if (zone.name or None) != (zones[zone.number].name or None):
            raise CodeplugError(f"zone {zone.number}: zone names cannot be changed (yet)")
        for ch in zone.channels:
            if ch.slot > size:
                raise CodeplugError(f"zone {zone.number}: slot {ch.slot} does not exist (max {size})")
            wanted[(zone.number, ch.slot)] = ch
    # Only zones listed in the YAML are compared – a zone left out stays as it is.
    listed = {z.number for z in cp.zones}
    have = {(z.number, ch.slot): ch for z in current.zones if z.number in listed for ch in z.channels}
    for key in sorted(set(have) | set(wanted)):
        old, new = have.get(key), wanted.get(key)
        if old == new:
            continue
        zone = zones[key[0]]
        start = spec.channels_offset + (zone.first_slot - 1 + key[1] - 1) * CHANNEL_SIZE
        if new is None:
            out[start : start + CHANNEL_SIZE] = b"\xff" * CHANNEL_SIZE
            continue
        where = f"zone {key[0]} slot {key[1]}"
        record = bytearray(spec.new_record if old is None else out[start : start + CHANNEL_SIZE])
        try:
            spec.encode_channel(record, old, new)
        except CodeplugError as e:
            raise CodeplugError(f"{where}: {e}") from None
        out[start : start + CHANNEL_SIZE] = record


def _patch_settings(out: bytearray, current: Codeplug, cp: Codeplug, spec: ImageMap) -> None:
    table = {s.key: s for s in spec.settings}
    start = spec.settings_offset
    section = bytearray(out[start : start + spec.settings_length])
    for key, value in cp.settings.values.items():
        if key not in current.settings.values:
            raise CodeplugError(f"settings: unknown key '{key}'")
        if plain(value) == plain(current.settings.values[key]):
            continue
        if key not in table:
            raise CodeplugError(f"settings.{key} is read only")
        setting = table[key]
        if setting.unset(section):
            # The whole byte is "not set" (FF): writing one setting defines the
            # byte, so every setting sharing it needs a value – never guess them.
            siblings = [s.key for s in spec.settings if s.offset == setting.offset and s.key != key]
            missing = [k for k in siblings if cp.settings.values.get(k) is None]
            if missing:
                raise CodeplugError(
                    f"settings.{key} shares a byte that is not set in the radio with "
                    f"{', '.join(missing)} – give them values too"
                )
        setting.encode(section, value)
    out[start : start + spec.settings_length] = section


def _check_unsupported(current: Codeplug, cp: Codeplug, encoded: tuple[str, ...] = ()) -> None:
    """Sections we cannot encode yet must be unchanged (or left out). ``encoded``:
    "section.key" paths the driver has already encoded (not compared here)."""
    for name, section in cp.vfo.items():
        if name not in current.vfo:
            raise CodeplugError(f"vfo: unknown VFO '{name}'")
        _same(f"vfo.{name}", plain(current.vfo[name]), plain(section))
    for key, value in cp.extra.items():
        if key not in current.extra:
            raise CodeplugError(f"unknown section '{key}'")
        old, new = plain(current.extra[key]), plain(value)
        for path in encoded:
            section, _, sub = path.partition(".")
            if section == key and isinstance(old, dict) and isinstance(new, dict):
                old.pop(sub, None)
                new.pop(sub, None)
        _same(key, old, new)


def _same(where: str, old, new) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        where = ", ".join(f"{where}.{k}" for k in changed)
    raise CodeplugError(f"changing {where} is not supported yet – restore the value read from the radio")


# --- helpers for the per-radio channel encoders --------------------------------


def changed(old: Channel | None, new: Channel, field: str) -> bool:
    return old is None or getattr(old, field) != getattr(new, field)


def extra_changed(old: Channel | None, new: Channel, key: str, default) -> bool:
    return old is None or old.extra.get(key, default) != new.extra.get(key, default)


def set_bit(record: bytearray, index: int, mask: int, on: bool) -> None:
    record[index] = (record[index] | mask) if on else (record[index] & ~mask & 0xFF)


def set_bits(record: bytearray, index: int, mask: int, shift: int, value: int) -> None:
    record[index] = (record[index] & ~(mask << shift) & 0xFF) | (value & mask) << shift


def check_extra(new: Channel, allowed: dict[str, tuple[type, int | None]]) -> None:
    """allowed: key → (type, max value for ints)."""
    for key, value in new.extra.items():
        if key not in allowed:
            raise CodeplugError(f"unknown extra key '{key}' (known: {', '.join(allowed)})")
        kind, maximum = allowed[key]
        if kind is bool and not isinstance(value, bool):
            raise CodeplugError(f"extra.{key} must be true or false")
        if kind is int and (
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum
        ):
            raise CodeplugError(f"extra.{key} must be a number 0–{maximum}")


def check_common(
    old: Channel | None, new: Channel, power_levels: tuple[str, ...], modes: tuple[str, ...] = ("FM", "AM")
) -> None:
    """Validate the fields that change; an odd value the radio holds itself
    (``?3``, ``?F000``) may stay as long as it is not touched."""
    if changed(old, new, "power") and new.power not in power_levels:
        raise CodeplugError(f"power {new.power!r} is not one of {' | '.join(power_levels)}")
    if changed(old, new, "mode") and new.mode not in modes:
        raise CodeplugError(f"mode {new.mode!r} is not one of {' | '.join(modes)}")
    for field in ("rx_tone", "tx_tone"):
        tone = getattr(new, field)
        if changed(old, new, field) and isinstance(tone, str) and tone.startswith("?"):
            raise CodeplugError(f"tone {tone} is an unknown code and cannot be written")


def fm_entries(value, slots: int, name_length: int, lo: float, hi: float) -> dict[int, dict]:
    """Validate a broadcast.fm list from YAML → {n: {"freq": MHz, "name": str}}."""
    if not isinstance(value, list):
        raise CodeplugError("broadcast.fm must be a list like [{n: 1, freq: 91.0}]")
    out: dict[int, dict] = {}
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("n"), int):
            raise CodeplugError("broadcast.fm: every entry needs 'n' (memory number)")
        n = entry["n"]
        if not 1 <= n <= slots or n in out:
            raise CodeplugError(f"broadcast.fm: n = {n} is out of range 1–{slots} or used twice")
        unknown = set(entry) - {"n", "freq", "name"}
        if unknown:
            raise CodeplugError(f"broadcast.fm n = {n}: unknown keys {', '.join(sorted(unknown))}")
        freq = entry.get("freq")
        if isinstance(freq, bool) or not isinstance(freq, int | float) or not lo <= freq <= hi:
            raise CodeplugError(f"broadcast.fm n = {n}: frequency must be {lo:g}–{hi:g} MHz, got {freq!r}")
        name = entry.get("name") or ""
        if name and not name_length:
            raise CodeplugError("broadcast.fm: this radio cannot store station names – leave 'name' out")
        out[n] = {"freq": float(freq), "name": str(name)}
    return out


def fhss_value(code) -> int | None:
    if code in (None, ""):
        return None
    try:
        value = int(str(code), 16)
    except ValueError:
        raise CodeplugError(f"extra.fhss must be 6 hex digits, got {code!r}") from None
    if not 0 <= value <= 0xFFFFFF:
        raise CodeplugError(f"extra.fhss must be 6 hex digits, got {code!r}")
    return value
