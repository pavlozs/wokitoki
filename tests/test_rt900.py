import pytest
from fakes import FakeRadtel, fake_rt900

from wokitoki.core.driver import DriverError
from wokitoki.drivers.radtel import rt900_map as m
from wokitoki.drivers.radtel.common import key_for_frame
from wokitoki.drivers.radtel.rt900 import RT900, SEND_FRAME


def blank_image() -> bytearray:
    return bytearray(b"\xff" * m.IMAGE_SIZE)


def put(image: bytearray, offset: int, data: bytes) -> None:
    image[offset : offset + len(data)] = data


def test_fixed_send_frame_selects_chirp_key():
    assert len(SEND_FRAME) == 25
    assert key_for_frame(SEND_FRAME) == b"CO 7"  # CHIRP: baofeng_uv17Pro._crypt(1, …)


@pytest.mark.parametrize(
    ("code", "tone"),
    [
        (0, "off"),
        (0xFFFF, "off"),
        (885, 88.5),
        (0x258, 60.0),
        (1, "D023N"),
        (0x69, "D754N"),
        (0x6A, "D023I"),
        (0x6A + 104, "D754I"),
    ],
)
def test_decode_tone(code, tone):
    assert m.decode_tone(code) == tone


def test_decode_channels():
    image = blank_image()
    # 1: 145.500 / 145.500-0.6, TX CTCSS 88.5, RX DCS 023N, narrow, mid, scan, bcl, FHSS
    put(image, 0, bytes([0x00, 0x00, 0x55, 0x14, 0x00, 0x00, 0x49, 0x14])
        + (1).to_bytes(2, "little") + (885).to_bytes(2, "little")
        + bytes([0x03, 0x01, (2 << 2) | 1, 0x40 | 0x08 | 0x04])
        + bytes([0x56, 0x34, 0x12, 0xA0]) + b"REPEATER".ljust(12, b"\xff"))  # fmt: skip
    # 3: airband AM, TX freq blank (FF) = no TX, low power, no scan, name with FF inside
    put(image, 64, bytes([0x00, 0x75, 0x93, 0x11]) + b"\xff" * 4 + b"\x00" * 4
        + bytes([0, 0, 0x02, 0x02]) + b"\xff" * 4 + b"TW\xffR".ljust(12, b"\xff"))  # fmt: skip
    cp = RT900().decode(bytes(image))
    (zone,) = cp.zones
    assert (zone.size, len(zone.channels)) == (999, 2)
    ch1, ch3 = zone.channels
    assert (ch1.slot, ch1.name, ch1.rx, ch1.tx) == (1, "REPEATER", 145_500_000, 144_900_000)
    assert (ch1.rx_tone, ch1.tx_tone, ch1.bw, ch1.power, ch1.mode, ch1.scan) == (
        "D023N", 88.5, "narrow", "mid", "FM", True,
    )  # fmt: skip
    assert ch1.extra == {"signal_group": 3, "ptt_id": 1, "scrambler": 2, "busy_lock": True, "fhss": "123456"}
    assert (ch3.slot, ch3.name, ch3.tx, ch3.mode, ch3.power, ch3.scan) == (
        3,
        "TW R",
        None,
        "AM",
        "low",
        False,
    )
    assert ch3.to_yaml()["duplex"] == "off"


def test_decode_vfo_settings_and_dtmf():
    image = blank_image()
    vfo_a = bytes([1, 4, 5, 5, 0, 0, 0, 0]) + b"\x00" * 4 + bytes([0, 0, 0x10, 0, 0x01, 0x40, 0, 4])
    put(image, m.VFO_OFFSET, vfo_a + bytes([0, 0, 0, 6, 0, 0]) + b"\x00" * 6)
    put(image, m.SETTINGS_OFFSET, bytes([4, 1, 0, 2, 1, 3]))
    put(image, m.SETTINGS_OFFSET + 0x19, bytes([0]))  # FM radio enabled (inverted)
    put(image, m.SETTINGS_OFFSET + 0x1A, bytes([0x10]))  # A = vfo, B = channel
    put(image, m.RADIO_MODE_OFFSET, bytes([0x66]))
    put(image, m.DTMF_OFFSET + 0x20, bytes([1, 2, 3, 0x0E, 0xFF]))
    cp = RT900().decode(bytes(image))
    a = cp.vfo["A"].values
    assert (a["rx"], a["shift"], a["offset"], a["power"], a["bw"], a["step"]) == (
        145.5,
        "+",
        0.6,
        "mid",
        "narrow",
        12.5,
    )
    st = cp.settings.values
    assert (st["sql"], st["save_mode"], st["auto_backlight"], st["tdr"], st["tot"]) == (
        4,
        "normal",
        "10s",
        True,
        "45s",
    )
    assert (st["fm_radio"], st["work_mode_a"], st["work_mode_b"]) == (True, "vfo", "channel")
    assert st["radio_mode"] == "pmr"
    assert st["beep_prompt"] is None  # 0xFF = unset
    assert cp.extra["dtmf"].values["groups"] == [{"n": 1, "code": "123B"}]


async def test_read_image_from_fake_radio():
    image = bytes(i * 13 & 0xFF for i in range(m.IMAGE_SIZE))
    radio = fake_rt900(image=image)
    await radio.open()
    seen = []
    driver = RT900()
    assert await driver.read_image(radio, lambda d, t: seen.append((d, t))) == image
    assert seen[-1] == (970, 970)
    assert driver.info.model == "RT-900" and driver.info.details["ident_known"]
    assert b"M" not in radio.written  # the RT-900 has no model command
    assert radio.written[2] == SEND_FRAME
    # calibration is read in plain text: the fake sends it unencrypted
    assert radio.written[-1] == b"E"


async def test_rt950_does_not_answer_rt900_magic():
    radio = FakeRadtel()  # RT-950 Pro fake: only PROGRAMBT9000U
    await radio.open()
    driver = RT900()
    driver.handshake = type(driver.handshake)(**{**driver.handshake.__dict__, "timeout": 0.1})
    with pytest.raises(DriverError, match="handshake"):
        await driver.identify(radio)
