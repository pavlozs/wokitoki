import datetime as dt

import pytest

from wokitoki.core.aliases import Alias, AliasError, AliasStore, validate_name

# Non-ASCII notes on purpose: UTF-8 must survive the round trip.
HAND_WRITTEN = """\
# My radios
aliases:
  rt950:   # the blue one (modrá)
    driver: radtel-rt950pro
    transport: ble
    address: A1B2C3D4-E5F6-4711-8899-AABBCCDDEEFF
    note: "Pavlova RT-950 Pro"
    added: 2026-09-26
  kabel:
    driver: radtel-rt900
    transport: serial
    port: COM3
"""


def test_missing_file_is_empty(tmp_path):
    store = AliasStore(tmp_path / "aliases.yaml")
    assert store.names() == []


def test_load_hand_written(tmp_path):
    path = tmp_path / "aliases.yaml"
    path.write_text(HAND_WRITTEN, encoding="utf-8")
    store = AliasStore(path)
    rt950 = store.get("rt950")
    assert rt950.driver == "radtel-rt950pro"
    assert rt950.target == "A1B2C3D4-E5F6-4711-8899-AABBCCDDEEFF"
    assert rt950.added == dt.date(2026, 9, 26)
    assert store.get("kabel").target == "COM3"


def test_round_trip_keeps_comments_and_order(tmp_path):
    path = tmp_path / "aliases.yaml"
    path.write_text(HAND_WRITTEN, encoding="utf-8")
    store = AliasStore(path)
    store.add(Alias("novy", "radtel-rt950pro", "ble", address="AA:BB", note="žlutá"))
    store.rename("rt950", "modra")
    store.save()
    text = path.read_text(encoding="utf-8")
    assert "# My radios" in text
    assert "žlutá" in text
    assert AliasStore(path).names() == ["modra", "kabel", "novy"]


def test_add_duplicate_and_replace(tmp_path):
    store = AliasStore(tmp_path / "a.yaml")
    store.add(Alias("x", "d", "ble", address="1"))
    with pytest.raises(AliasError, match="already exists"):
        store.add(Alias("x", "d", "ble", address="2"))
    store.add(Alias("x", "d", "ble", address="2"), replace=True)
    assert store.get("x").address == "2"


def test_remove(tmp_path):
    store = AliasStore(tmp_path / "a.yaml")
    store.add(Alias("x", "d", "serial", port="COM3"))
    store.remove("x")
    with pytest.raises(AliasError):
        store.get("x")


@pytest.mark.parametrize("name", ["rt950", "cb-rucka", "a_1", "9"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "RT950", "cb handheld", "a/b", "c:", "-x", "ž", "x" * 33])
def test_invalid_names(name):
    with pytest.raises(AliasError):
        validate_name(name)


def test_unknown_key_is_error(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text("aliases:\n  x: {driver: d, transport: ble, adress: 1}\n", encoding="utf-8")
    with pytest.raises(AliasError, match="adress"):
        AliasStore(path).get("x")


def test_ble_alias_needs_address():
    with pytest.raises(AliasError, match="address"):
        Alias("x", "d", "ble").validate()


class _ObjCString(str):
    """Stands in for pyobjc_unicode, which bleak returns on macOS."""


def test_str_subclass_values_are_saved_as_plain_str(tmp_path):
    path = tmp_path / "a.yaml"
    store = AliasStore(path)
    address = _ObjCString("A1B2C3D4-E5F6-4711-8899-AABBCCDDEEFF")
    store.add(Alias("rt950", "radtel-rt950pro", "ble", address=address, device_name=_ObjCString("RT-950")))
    store.save()
    assert AliasStore(path).get("rt950").address == "A1B2C3D4-E5F6-4711-8899-AABBCCDDEEFF"
    assert not (tmp_path / "a.yaml.tmp").exists()
