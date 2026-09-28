import glob
from pathlib import Path

import pytest
from fakes import FakeUVK5
from typer.testing import CliRunner

from wokitoki import cli
from wokitoki.core import codeplug
from wokitoki.core.codeplug import CodeplugError
from wokitoki.core.driver import DriverError
from wokitoki.core.transport import SerialPortInfo
from wokitoki.drivers.quansheng import protocol
from wokitoki.drivers.quansheng import uvk5_map as m
from wokitoki.drivers.quansheng.uvk5 import UVK5F4HWN

REAL = [f for f in glob.glob(str(Path.home() / "wokitoki/*/*.img")) if Path(f).stat().st_size == m.IMAGE_SIZE]


def image() -> bytearray:
    img = bytearray(b"\xff" * m.IMAGE_SIZE)
    img[m.ATTR_OFFSET : m.ATTR_OFFSET + 207] = bytes([m.EMPTY_ATTR]) * 207
    img[m.BUILD_OPTIONS : m.BUILD_OPTIONS + 2] = bytes([0x20, 0x33])  # like our radio: WIDE_RX on
    # slot 1: PMR1 446.00625, narrow, user power, step 6.25, scan list 1, band 5
    img[0:16] = (44_600_625).to_bytes(4, "little") + bytes.fromhex("000000000000000002000200")
    img[m.ATTR_OFFSET] = 0x25
    img[m.NAME_OFFSET : m.NAME_OFFSET + 16] = b"PMR1".ljust(16, b"\x00")
    img[0xE71] = 4  # squelch
    for shared in (0xE74, 0xE78, 0xE7E, 0xE90, 0xF47, 0x1FF4, 0x1FF6, 0x1FF7):
        img[shared] = 0x00  # bytes holding several settings are set in a real radio
    return img


def roundtrip(img: bytes, tmp_path, edit=None) -> bytes:
    y = tmp_path / "k5.yaml"
    codeplug.save(UVK5F4HWN().decode(img), y)
    if edit:
        y.write_text(edit(y.read_text(encoding="utf-8")), encoding="utf-8")
    return UVK5F4HWN().encode(codeplug.load(y), bytes(img))


def test_crc_and_frame():
    assert protocol.crc16_xmodem(b"123456789") == 0x31C3  # CRC-16/XMODEM check value
    frame = protocol.frame(protocol.command(protocol.CMD_HELLO, protocol.SESSION))
    assert frame[:4] == b"\xab\xcd\x08\x00" and frame[-2:] == b"\xdc\xba"
    assert protocol.xor(frame[4:12]) == b"\x14\x05\x04\x00\x6a\x39\x57\x64"  # CHIRP's hello packet


def test_decode_channel():
    cp = UVK5F4HWN().decode(bytes(image()))
    (ch,) = cp.zones[0].channels
    assert (ch.slot, ch.name, ch.rx, ch.tx, ch.bw, ch.power, ch.scan) == (
        1,
        "PMR1",
        446_006_250,
        446_006_250,
        "narrow",
        "user",
        True,
    )
    assert ch.extra == {"step": 6.25}
    assert cp.settings.values["squelch"] == 4
    assert cp.extra["firmware_features"].values["wide_rx"] is True


def test_blank_and_real_round_trip(tmp_path):
    for img in [bytes(image())] + [Path(f).read_bytes() for f in REAL]:
        assert roundtrip(img, tmp_path) == img


def test_new_channel_sets_band_attr_and_name(tmp_path):
    add = '      - {slot: 3, name: "AIR", rx: 118.055, mode: AM, bw: wide, power: low1, scan: false, extra: {step: 8.33}}\n'
    new = roundtrip(image(), tmp_path, lambda y: y.replace("    channels:\n", "    channels:\n" + add, 1))
    rec = new[32:48]
    assert int.from_bytes(rec[0:4], "little") == 11_805_500 and rec[11] == 0x10  # AM, simplex
    assert (rec[12] >> 2) & 7 == 1 and rec[14] == m.STEPS_KHZ.index(8.33)
    assert new[m.ATTR_OFFSET + 2] == 0x01  # no scan list, band 1 (108–137 MHz)
    assert new[m.NAME_OFFSET + 32 : m.NAME_OFFSET + 48] == b"AIR".ljust(16, b"\x00")


def test_delete_and_power_change(tmp_path):
    new = roundtrip(image(), tmp_path, lambda y: y.replace("power: user", "power: high"))
    assert [i for i in range(m.IMAGE_SIZE) if new[i] != image()[i]] == [12]
    new = roundtrip(
        image(), tmp_path, lambda y: "\n".join(l for l in y.splitlines() if "slot: 1," not in l) + "\n"
    )
    assert new[0:16] == b"\xff" * 16 and new[m.ATTR_OFFSET] == m.EMPTY_ATTR
    assert new[m.NAME_OFFSET : m.NAME_OFFSET + 16] == b"\xff" * 16


def test_offset_and_tones(tmp_path):
    new = roundtrip(
        image(),
        tmp_path,
        lambda y: y.replace("rx: 446.00625,", "rx: 145.6, offset: -0.6, rx_tone: 88.5, tx_tone: D023I,"),
    )
    rec = new[0:16]
    assert int.from_bytes(rec[4:8], "little") == 60_000 and rec[11] & 0x0F == 2  # minus 600 kHz
    assert rec[10] == 0x31 and rec[8] == m.CTCSS.index(88.5) and rec[9] == 0  # TX DCS inverted, RX CTCSS
    assert new[m.ATTR_OFFSET] & 0x07 == 2  # band moved to 137–174 MHz


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda y: y.replace("rx: 446.00625,", "rx: 446.00625, duplex: off,"), "tx_lock"),
        (lambda y: y.replace("rx: 446.00625,", "rx: 446.00625, rx_tone: 88.4,"), "tone list"),
        (lambda y: y.replace('"PMR1"', '"ELEVEN CHAR"'), "allows 10"),
        (
            lambda y: y.replace(
                "scan: true, extra: {step: 6.25}", "scan: false, extra: {step: 6.25, scan_lists: [2]}"
            ),
            "scan",
        ),
        (lambda y: y.replace("power: user", "power: mid5"), "power"),
        (lambda y: y.replace("wide_rx: true", "wide_rx: false"), "firmware_features.wide_rx"),
    ],
)
def test_invalid_edits(edit, message, tmp_path):
    with pytest.raises(CodeplugError, match=message):
        roundtrip(image(), tmp_path, edit)


async def test_read_and_write_with_fake_radio():
    radio = FakeUVK5(bytes(image()))
    await radio.open()
    driver = UVK5F4HWN()
    assert await driver.read_image(radio) == bytes(image())
    assert driver.info.firmware == "F4HWN v4.2"
    target = bytearray(image())
    target[12] = 0x1E  # high power
    await driver.write_image(radio, bytes(target))
    assert bytes(radio.eeprom) == bytes(target)
    assert radio.writes[:2] == [0x0000, 0x0080] and radio.writes[-1] == 0x1FF2
    assert all(o < 0x1D00 or o == 0x1FF2 for o in radio.writes) and radio.resets == 1


async def test_refuses_to_write_other_firmware():
    radio = FakeUVK5(bytes(image()), firmware="k5_v2.01.32")
    await radio.open()
    with pytest.raises(DriverError, match="not F4HWN v4"):
        await UVK5F4HWN().write_image(radio, bytes(image()))
    assert not radio.writes


def test_scan_identifies_uvk5(monkeypatch):
    radio = FakeUVK5(bytes(image()))
    monkeypatch.setattr(
        cli, "list_serial_ports", lambda: [SerialPortInfo("/dev/x", "", 0x1A86, 0x7523, None)]
    )
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: radio)
    result = CliRunner().invoke(cli.app, ["--plain", "scan", "--serial"])
    assert "UV-K5 via CH340 cable\t/dev/x\t\tquansheng-uvk5-f4hwn (identified)" in result.output


async def test_noise_before_a_frame_is_skipped():
    radio = FakeUVK5(bytes(image()))
    await radio.open()
    radio._feed(b"\x98\xdf\x16\x7f")  # what our radio sent while booting after a reset
    orig = radio.reset_input
    radio.reset_input = lambda: None  # keep the noise in the buffer
    try:
        assert (await protocol.hello(radio)).firmware == "F4HWN v4.2"
    finally:
        radio.reset_input = orig
