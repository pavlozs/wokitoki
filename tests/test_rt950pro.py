import os
from pathlib import Path

import pytest
from fakes import FakeRadtel
from ruamel.yaml import YAML

from wokitoki.core import codeplug
from wokitoki.drivers.radtel import rt950pro_map as m
from wokitoki.drivers.radtel.rt950pro import RT950Pro

# Real backups from the RT950Pro macOS app (not in the repo – personal data).
BACKUP_DIR = Path(os.environ.get("WOKITOKI_TEST_IMAGES", Path.home() / "Documents"))
BACKUPS = sorted(BACKUP_DIR.glob("RT-950Pro_*.img"))


def blank_image() -> bytearray:
    return bytearray(b"\xff" * m.IMAGE_SIZE)


def put_channel(image: bytearray, slot: int, record: bytes) -> None:
    start = (slot - 1) * m.CHANNEL_SIZE
    image[start : start + m.CHANNEL_SIZE] = record


def channel(rx: bytes, tx: bytes, tones: bytes, b12_15: bytes, fhss: bytes, name: bytes) -> bytes:
    record = rx + tx + tones + b12_15 + fhss + name.ljust(12, b"\xff")
    assert len(record) == 32
    return record


def test_codecs():
    # 10 Hz units, least significant pair first: 145.525 MHz = 14552500
    assert m.decode_bcd_frequency(bytes([0x00, 0x25, 0x55, 0x14])) == 145_525_000
    assert m.decode_bcd_frequency(b"\xff\xff\xff\xff") is None
    assert m.decode_bcd_frequency(b"\x5a\x00\x00\x00") is None
    assert m.decode_tone(0, 0) == "off"
    assert m.decode_tone(0x75, 0x03) == 88.5
    assert m.decode_tone(1, 0) == "D023N"
    assert m.decode_tone(len(m.DCS_BASE) + 1, 0) == "D023I"
    assert m.decode_fhss(bytes([0x56, 0x34, 0x12, 0xA0])) == "123456"
    assert m.decode_fhss(b"\x00\x00\x00\x00") is None
    assert m.decode_text(b"Praha" + b"\xff\xff") == "Praha"
    assert m.decode_text("北京".encode("gb2312") + b"\x00") == "北京"


def test_decode_synthetic_image():
    image = blank_image()
    # zone 1 / slot 1: 145.500 RX, 145.500-0.6 TX, CTCSS 88.5 TX, narrow, mid, scan, TX on
    put_channel(
        image, 1,
        channel(bytes([0x00, 0x00, 0x55, 0x14]), bytes([0x00, 0x00, 0x49, 0x14]), b"\x00\x00\x75\x03",
                bytes([0, 0, 0x01, 0x46]), b"\x00" * 4, b"R0"),
    )  # fmt: skip
    # zone 2 / slot 99 = global 198: airband AM, TX disabled, DCS on RX, scrambler 3, FHSS
    put_channel(
        image, 198,
        channel(bytes([0x00, 0x75, 0x93, 0x11]), bytes([0x00, 0x75, 0x93, 0x11]), b"\x01\x00\x00\x00",
                bytes([0, 0, 0x32, 0x05]), bytes([0x56, 0x34, 0x12, 0xA0]), b"TWR"),
    )  # fmt: skip
    cp = RT950Pro().decode(bytes(image))
    assert [len(z.channels) for z in cp.zones] == [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert cp.zones[9].size == 69
    r0 = cp.zones[0].channels[0]
    assert (r0.slot, r0.name, r0.rx, r0.tx) == (1, "R0", 145_500_000, 144_900_000)
    assert (r0.bw, r0.power, r0.scan, r0.tx_tone, r0.rx_tone) == ("narrow", "mid", True, 88.5, "off")
    twr = cp.zones[1].channels[0]
    assert (twr.slot, twr.mode, twr.tx, twr.power, twr.scan) == (99, "AM", None, "low", True)
    assert twr.rx_tone == "D023N"
    assert twr.extra == {"scrambler": 3, "fhss": "123456"}

    y = r0.to_yaml()
    assert dict(y) == {
        "slot": 1, "name": "R0", "rx": 145.5, "offset": -0.6, "mode": "FM", "bw": "narrow",
        "power": "mid", "tx_tone": 88.5, "scan": True,
    }  # fmt: skip
    assert twr.to_yaml()["duplex"] == "off"
    # unset settings (0xFF) are null
    assert cp.settings.values["sql"] is None


def test_yaml_is_loadable(tmp_path):
    cp = RT950Pro().decode(bytes(blank_image()))
    cp.alias, cp.image = "rt950", "rt950.img"
    path = tmp_path / "x.yaml"
    codeplug.save(cp, path)
    doc = YAML().load(path.read_text(encoding="utf-8"))
    assert doc["wokitoki"] == 1
    assert doc["radio"]["image"] == "rt950.img"
    assert len(doc["zones"]) == 10
    assert set(doc["vfo"]) == {"A", "B", "C"}


def test_wrong_size_is_rejected():
    with pytest.raises(Exception, match="image size"):
        RT950Pro().decode(b"\xff" * 100)


async def test_read_image_from_fake_radio():
    image = bytes(i * 7 & 0xFF for i in range(m.IMAGE_SIZE))
    radio = FakeRadtel(image=image)
    await radio.open()
    seen = []
    driver = RT950Pro()
    result = await driver.read_image(radio, lambda done, total: seen.append((done, total)))
    assert result == image
    assert seen[-1] == (259, 259)
    assert driver.info.model == "RT-950"
    assert radio.written[-1] == b"E"


@pytest.mark.skipif(not BACKUPS, reason="no RT950Pro .img backups found")
@pytest.mark.parametrize("path", BACKUPS, ids=lambda p: p.name)
async def test_real_backup_reads_back_identically(path):
    image = path.read_bytes()
    radio = FakeRadtel(image=image)
    await radio.open()
    assert await RT950Pro().read_image(radio) == image
    cp = RT950Pro().decode(image)
    assert cp.channel_count > 0


def test_broadcast_names_and_units():
    image = blank_image()
    params = m.MODULATION_OFFSET
    image[params : params + 2] = (9250).to_bytes(2, "little")  # FM 1: 92.5 MHz (10 kHz units)
    image[params + 34 : params + 36] = (9770).to_bytes(2, "little")  # AM 1: 9770 kHz (1 kHz units)
    image[params + 69 : params + 71] = (7144).to_bytes(2, "little")  # SSB 1: 7144 kHz
    image[params + 71 : params + 74] = b"\x00\x00\x00"
    names = m.MODULATION_NAMES_OFFSET
    image[names : names + 7] = b"Radio 1"
    b = m.decode_broadcast(image[params : params + 256], image[names : names + 0x300]).values
    assert b["fm"] == [{"n": 1, "freq": 92.5, "name": "Radio 1"}]
    assert b["am"] == [{"n": 1, "freq": 9.77, "name": ""}]  # empty name is still shown
    assert b["ssb"] == [{"n": 1, "freq": 7.144, "bfo": 0, "name": ""}]


def test_free_text_is_double_quoted(tmp_path):
    image = blank_image()
    put_channel(
        image, 1,
        channel(bytes([0x00, 0x00, 0x55, 0x14]), bytes([0x00, 0x00, 0x55, 0x14]), b"\x00" * 4,
                bytes([0, 0, 0, 0x06]), b"\x00" * 4, b"123"),
    )  # fmt: skip
    put_channel(
        image, 2,
        channel(bytes([0x00, 0x00, 0x55, 0x14]), bytes([0x00, 0x00, 0x55, 0x14]), b"\x00" * 4,
                bytes([0, 0, 0, 0x06]), b"\x00" * 4, b""),
    )  # fmt: skip
    path = tmp_path / "x.yaml"
    codeplug.save(RT950Pro().decode(bytes(image)), path)
    text = path.read_text(encoding="utf-8")
    assert '{slot: 1, name: "123", rx: 145.5,' in text
    assert '{slot: 2, name: "", rx: 145.5,' in text
    assert 'callsign: ""' in text
    assert "sql: null" in text  # unset setting stays null, not ""
    assert YAML().load(text)["zones"][0]["channels"][0]["name"] == "123"
