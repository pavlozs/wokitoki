"""Regression tests for the pre-release review: writing must never change more
than the YAML asks for, and bad input must end in a clear error."""

import asyncio

import pytest
from test_uvk5 import image as k5_image
from typer.testing import CliRunner

from wokitoki import cli
from wokitoki.core import codeplug
from wokitoki.core.aliases import AliasError, AliasStore
from wokitoki.core.codeplug import CodeplugError, Section, parse_channel
from wokitoki.core.driver import DriverError
from wokitoki.core.transport import TransportError
from wokitoki.core.transport.base import BufferedTransport
from wokitoki.drivers.quansheng import protocol
from wokitoki.drivers.quansheng import uvk5_map as k5
from wokitoki.drivers.quansheng.uvk5 import UVK5F4HWN
from wokitoki.drivers.radtel import rt950pro_map as m950
from wokitoki.drivers.radtel.common import read_block
from wokitoki.drivers.radtel.encoder import check_common
from wokitoki.drivers.radtel.rt900 import RT900
from wokitoki.drivers.radtel.rt950pro import RT950Pro

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("WOKITOKI_HOME", str(tmp_path / "data"))
    return tmp_path


def invoke(*args, input=None):
    return runner.invoke(cli.app, ["--plain", *args], input=input)


def ch(slot, rx, **kw):
    return parse_channel({"slot": slot, "rx": rx, **kw}, f"slot {slot}")


def rt950_with_two_zones() -> bytes:
    driver = RT950Pro()
    base = bytes(b"\xff" * m950.IMAGE_SIZE)
    cp = driver.decode(base)
    cp.zones[0].channels = [ch(1, 145.5, name="Z1")]
    cp.zones[1].channels = [ch(1, 433.5, name="Z2")]
    cp.zones_given = True
    return driver.encode(cp, base)


# --- channels ---------------------------------------------------------------


def test_zone_left_out_of_the_yaml_is_kept():
    driver = RT950Pro()
    image = rt950_with_two_zones()
    cp = driver.decode(image)
    cp.zones = [cp.zones[1]]  # the YAML lists zone 2 only
    cp.zones[0].channels[0].name = "Z2 new"
    out = driver.decode(driver.encode(cp, image))
    assert [c.name for c in out.zones[0].channels] == ["Z1"]
    assert [c.name for c in out.zones[1].channels] == ["Z2 new"]


def test_listed_zone_still_deletes_missing_channels():
    driver = RT950Pro()
    image = rt950_with_two_zones()
    cp = driver.decode(image)
    cp.zones[1].channels = []
    out = driver.decode(driver.encode(cp, image))
    assert out.channel_count == 1 and out.zones[1].channels == []


def test_uvk5_empty_zone_list_keeps_channels():
    driver = UVK5F4HWN()
    base = bytes(k5_image())
    cp = driver.decode(base)
    cp.zones = []
    assert driver.encode(cp, base) == base


def test_unknown_value_the_radio_holds_does_not_block_other_edits():
    old = ch(1, 145.5, power="?3", rx_tone="?F000")
    new = ch(1, 145.5, power="?3", rx_tone="?F000", name="renamed")
    check_common(old, new, ("high", "mid", "low"))  # untouched odd values are fine
    with pytest.raises(CodeplugError, match="power"):
        check_common(old, ch(1, 145.5, power="?2", rx_tone="?F000"), ("high", "mid", "low"))
    with pytest.raises(CodeplugError, match="unknown code"):
        check_common(None, new, ("?3",))


def test_mode_is_checked_by_the_driver():
    assert parse_channel({"slot": 1, "rx": 14.2, "mode": "USB"}, "x").mode == "USB"
    with pytest.raises(CodeplugError, match="modulation"):
        parse_channel({"slot": 1, "rx": 14.2, "mode": 5}, "x")
    driver = RT950Pro()
    base = bytes(b"\xff" * m950.IMAGE_SIZE)
    cp = driver.decode(base)
    cp.zones[0].channels = [ch(1, 145.5, mode="USB")]
    with pytest.raises(CodeplugError, match="mode 'USB'"):
        driver.encode(cp, base)


def test_uvk5_usb_channel_round_trips(tmp_path):
    driver = UVK5F4HWN()
    base = bytearray(k5_image())
    base[11] = (base[11] & 0x0F) | 2 << 4  # slot 1 → USB
    cp = driver.decode(bytes(base))
    assert cp.zones[0].channels[0].mode == "USB"
    path = tmp_path / "k5.yaml"
    codeplug.save(cp, path)
    assert driver.encode(codeplug.load(path), bytes(base)) == bytes(base)


def test_uvk5_rename_keeps_undecoded_name_bytes():
    driver = UVK5F4HWN()
    base = bytearray(k5_image())
    base[k5.NAME_OFFSET + 10 : k5.NAME_OFFSET + 16] = b"\xaa" * 6
    cp = driver.decode(bytes(base))
    cp.zones[0].channels[0].name = "PMR 1"
    out = driver.encode(cp, bytes(base))
    assert out[k5.NAME_OFFSET : k5.NAME_OFFSET + 10] == b"PMR 1".ljust(10, b"\x00")
    assert out[k5.NAME_OFFSET + 10 : k5.NAME_OFFSET + 16] == b"\xaa" * 6


# --- settings ---------------------------------------------------------------


def test_setting_in_an_unset_shared_byte_needs_all_siblings():
    driver = RT950Pro()
    base = bytes(b"\xff" * m950.IMAGE_SIZE)
    cp = driver.decode(base)
    cp.settings.values["work_mode_a"] = "frequency"
    with pytest.raises(CodeplugError, match="work_mode_b, work_mode_c"):
        driver.encode(cp, base)
    cp.settings.values["work_mode_b"] = "channel"
    cp.settings.values["work_mode_c"] = "channel"
    out = driver.decode(driver.encode(cp, base)).settings.values
    assert (out["work_mode_a"], out["work_mode_b"], out["work_mode_c"]) == ("frequency", "channel", "channel")


def test_setting_keeps_sibling_bits_in_a_set_byte():
    driver = UVK5F4HWN()
    base = bytearray(k5_image())
    base[0x1FF5] = 0xFF  # classic, classic, keys+ptt, inverted, contrast 15 – a real value
    cp = driver.decode(bytes(base))
    values = cp.settings.values
    assert (values["gui_style"], values["invert_display"], values["contrast"]) == ("classic", True, 15)
    values["contrast"] = 8
    assert driver.encode(cp, bytes(base))[0x1FF5] == 0xF8


# --- FM memories ------------------------------------------------------------


def test_fm_without_names_and_unsorted_is_accepted():
    driver = RT950Pro()
    base = bytes(b"\xff" * m950.IMAGE_SIZE)
    cp = driver.decode(base)
    cp.extra["broadcast"].values["fm"] = [{"n": 2, "freq": 91.5}, {"n": 1, "freq": 88.0}]
    fm = driver.decode(driver.encode(cp, base)).extra["broadcast"].values["fm"]
    assert [(e["n"], e["freq"]) for e in fm] == [(1, 88.0), (2, 91.5)]


def test_uvk5_fm_with_empty_name_is_accepted():
    driver = UVK5F4HWN()
    base = bytes(k5_image())
    cp = driver.decode(base)
    cp.extra["broadcast"] = Section(values={"fm": [{"n": 1, "freq": 88.0, "name": ""}]})
    fm = driver.decode(driver.encode(cp, base)).extra["broadcast"].values["fm"]
    assert fm == [{"n": 1, "freq": 88.0}]


# --- protocols and transports -----------------------------------------------


class Scripted(BufferedTransport):
    """Answers every write with the next scripted reply."""

    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)

    description = "scripted"

    async def open(self):
        self._connected = True

    async def close(self):
        self._connected = False

    async def write(self, data, *, confirm=False):
        if self.replies:
            self._feed(self.replies.pop(0))


def test_block_from_another_address_is_never_stored():
    wrong = bytes([0x52, 0x0F, 0x80, 4]) + b"\x00" * 4
    right = bytes([0x52, 0x10, 0x00, 4]) + b"\x01\x02\x03\x04"

    async def run(replies):
        t = Scripted(replies)
        await t.open()
        return await read_block(t, 0x52, 0x1000, 4, None, timeout=0.2)

    assert asyncio.run(run([wrong, right])) == b"\x01\x02\x03\x04"
    with pytest.raises(DriverError, match="does not match"):
        asyncio.run(run([wrong, wrong, wrong]))


def test_short_uvk5_reply_is_a_driver_error():
    short = b"\xab\xcd\x02\x00" + protocol.xor(b"\x15\x05\x00\x00") + b"\xdc\xba"

    async def run():
        t = Scripted([short])
        await t.open()
        return await protocol.hello(t, timeout=0.2)

    with pytest.raises(DriverError, match="too short"):
        asyncio.run(run())


def test_buffered_transport_works_across_event_loops():
    t = Scripted([])

    async def wait_for_nothing():
        await t.open()
        with pytest.raises(TransportError):
            await t.read_exact(1, 0.05)

    asyncio.run(wait_for_nothing())
    asyncio.run(wait_for_nothing())  # used to fail: Event bound to a different loop


# --- files and aliases ------------------------------------------------------


def test_broken_alias_file_is_a_clear_error(tmp_path):
    path = tmp_path / "aliases.yaml"
    path.write_text("aliases:\n  rt950: {driver: x\n", encoding="utf-8")
    with pytest.raises(AliasError, match="not valid YAML"):
        AliasStore(path)
    path.write_text(
        "aliases:\n  123: {driver: radtel-rt900, transport: serial, port: COM3}\n", encoding="utf-8"
    )
    with pytest.raises(AliasError, match="quoted"):
        AliasStore(path)


def test_read_output_must_not_be_the_image():
    result = invoke("read", "--driver", "radtel-rt900", "--port", "COM9", "-o", "backup.img")
    assert result.exit_code == 1 and "YAML" in result.output


def test_decode_output_must_not_replace_the_image(tmp_path):
    img = tmp_path / "x.img"
    img.write_bytes(b"\xff" * RT900.image_size)
    result = invoke("decode", str(img), "-o", str(img), "--force")
    assert result.exit_code == 1 and img.read_bytes() == b"\xff" * RT900.image_size


def test_write_missing_file():
    result = invoke("write", "--driver", "radtel-rt900", "--port", "COM9", "missing.yaml")
    assert result.exit_code == 1 and "does not exist" in result.output


def test_write_refuses_alias_that_is_a_path(tmp_path):
    img = tmp_path / "x.img"
    img.write_bytes(b"\xff" * RT900.image_size)
    yaml = tmp_path / "x.yaml"
    assert invoke("decode", str(img), "-o", str(yaml)).exit_code == 0
    text = yaml.read_text(encoding="utf-8").replace(
        "  driver: radtel-rt900\n", '  driver: radtel-rt900\n  alias: "../../escape"\n'
    )
    yaml.write_text(text, encoding="utf-8")
    result = invoke("write", "--driver", "radtel-rt900", "--port", "COM9", str(yaml))
    assert result.exit_code == 1 and "invalid alias" in result.output


def test_profile_apply_save_does_not_overwrite(tmp_path):
    existing = tmp_path / "mine.yaml"
    existing.write_text("keep me\n", encoding="utf-8")
    result = invoke("profile", "apply", "--driver", "radtel-rt900", "--port", "COM9", "--save", str(existing))
    assert result.exit_code == 1 and "already exists" in result.output
    assert existing.read_text(encoding="utf-8") == "keep me\n"
