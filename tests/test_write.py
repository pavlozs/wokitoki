"""Encoding (patching images) and writing – no radio needed."""

import glob
from pathlib import Path

import pytest
from fakes import FakeRadtel, fake_image, fake_rt900
from typer.testing import CliRunner

from wokitoki import cli
from wokitoki.core import codeplug
from wokitoki.core.codeplug import CodeplugError
from wokitoki.core.driver import DriverError
from wokitoki.drivers.radtel import rt900_map, rt950pro_map
from wokitoki.drivers.radtel.rt900 import RT900
from wokitoki.drivers.radtel.rt950pro import RT950Pro

HOME = Path.home()
REAL_IMAGES = [(RT950Pro, f) for f in glob.glob(str(HOME / "Documents/RT-950Pro_*.img"))]
REAL_IMAGES += [(RT950Pro, f) for f in glob.glob(str(HOME / "wokitoki/rt950pro/*.img"))]
REAL_IMAGES += [(RT900, f) for f in glob.glob(str(HOME / "wokitoki/rt900/*.img")) if "before-write" not in f]


def rt900_image() -> bytes:
    image = bytearray(b"\xff" * rt900_map.IMAGE_SIZE)
    # slot 1: PMR 1, narrow, low, scan
    image[0:32] = bytes.fromhex("25066044250660440000000000000244ffffffff") + b"PMR1".ljust(12, b"\xff")
    image[rt900_map.SETTINGS_OFFSET : rt900_map.SETTINGS_OFFSET + 6] = bytes([4, 1, 0, 2, 1, 3])
    return bytes(image)


def roundtrip_yaml(driver, image: bytes, tmp_path: Path, edit=None) -> bytes:
    path = tmp_path / "x.yaml"
    codeplug.save(driver.decode(image), path)
    if edit:
        path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
    return driver.encode(codeplug.load(path), image)


@pytest.mark.parametrize("driver_cls", [RT950Pro, RT900])
def test_blank_image_round_trip(driver_cls, tmp_path):
    image = bytes([0xFF]) * driver_cls.image_size
    assert roundtrip_yaml(driver_cls(), image, tmp_path) == image


@pytest.mark.skipif(not REAL_IMAGES, reason="no real backups on this machine")
@pytest.mark.parametrize(
    ("driver_cls", "path"), REAL_IMAGES, ids=lambda x: Path(x).name if isinstance(x, str) else ""
)
def test_real_backup_survives_yaml_round_trip(driver_cls, path, tmp_path):
    image = Path(path).read_bytes()
    assert roundtrip_yaml(driver_cls(), image, tmp_path) == image


def test_rt900_edits_touch_only_their_bytes(tmp_path):
    image = rt900_image()
    new = roundtrip_yaml(
        RT900(), image, tmp_path,
        lambda y: y.replace("power: low", "power: mid").replace("sql: 4 ", "sql: 7 "),
    )  # fmt: skip
    changed = [i for i in range(len(image)) if image[i] != new[i]]
    assert changed == [14, rt900_map.SETTINGS_OFFSET]
    assert new[14] & 0x03 == 1 and new[rt900_map.SETTINGS_OFFSET] == 7


def test_rt900_add_and_delete_channel(tmp_path):
    image = rt900_image()
    add = '      - {slot: 5, name: "NEW", rx: 145.5, offset: -0.6, mode: FM, bw: narrow, power: high, tx_tone: 88.5, scan: false}\n'

    def edit(y: str) -> str:
        lines = y.splitlines(keepends=True)
        i = next(n for n, line in enumerate(lines) if "slot: 1," in line)
        return "".join(lines[:i] + [add] + lines[i + 1 :])  # delete slot 1, add slot 5

    new = roundtrip_yaml(RT900(), image, tmp_path, edit)
    assert new[0:32] == b"\xff" * 32
    rec = new[4 * 32 : 5 * 32]
    assert rec[0:8] == bytes.fromhex("0000551400004914")
    assert rec[8:12] == bytes.fromhex("00007503")  # RX off, TX 88.5 Hz
    assert rec[14] == 0 and rec[15] == 0x40  # high, narrow, no scan
    assert rec[16:20] == b"\xff" * 4 and rec[20:32] == b"NEW".ljust(12, b"\xff")
    ch = RT900().decode(new).zones[0].channels[0]
    assert (ch.slot, ch.tx, ch.tx_tone, ch.scan) == (5, 144_900_000, 88.5, False)


def test_rt950_new_channel_like_the_radio(tmp_path):
    image = bytes([0xFF]) * rt950pro_map.IMAGE_SIZE
    add = '    channels:\n      - {slot: 2, name: "CB 01", rx: 26.965, mode: FM, bw: wide, power: high, scan: true}\n'
    new = roundtrip_yaml(RT950Pro(), image, tmp_path, lambda y: y.replace("    channels: []\n", add, 1))
    rec = new[32:64]
    # a CB channel created by the radio itself: byte 14 = 00, byte 15 = 06, FHSS 00×4
    assert rec[14] == 0x00 and rec[15] == 0x06 and rec[16:20] == b"\x00" * 4
    assert rec[20:32] == b"CB 01".ljust(12, b"\xff")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda y: y.replace("power: low", "power: turbo"), "power 'turbo'"),
        (lambda y: y.replace('name: "PMR1"', 'name: "THIS NAME IS TOO LONG"'), "allows 12"),
        (lambda y: y.replace('name: "PMR1"', 'name: "Kanál"'), "only ASCII"),
        (lambda y: y.replace("scan: true}", "scan: true, extra: {turbo: 1}}"), "unknown extra key"),
        (lambda y: y.replace("rx: 446.00625", "rx: 446.006253"), "10 Hz steps"),
        (lambda y: y.replace("radio_mode: default", "radio_mode: gmrs"), "read only"),
        (lambda y: y.replace("rx: null, shift", "rx: 145.5, shift", 1), "vfo.A.rx is not supported"),
        (lambda y: y.replace("slot: 1,", "slot: 1000,"), "slot 1000 does not exist"),
    ],
)
def test_rt900_invalid_edits(edit, message, tmp_path):
    with pytest.raises(CodeplugError, match=message):
        roundtrip_yaml(RT900(), rt900_image(), tmp_path, edit)


def test_check_write_refuses_calibration():
    driver = RT900()
    image = rt900_image()
    target = bytearray(image)
    target[0xF010] ^= 0xFF
    with pytest.raises(DriverError, match="outside the writable areas"):
        driver.check_write(image, bytes(target))


async def _write_and_compare(driver, radio, target):
    await radio.open()
    await driver.write_image(radio, target)
    assert radio.written[-1] == b"E"
    return fake_image(radio)


async def test_rt900_write_image():
    image = rt900_image()
    radio = fake_rt900(image=image)
    target = bytearray(image)
    target[14] = 0x01
    target[0xA020] = 0x05
    got = await _write_and_compare(RT900(), radio, bytes(target))
    assert got == bytes(target)
    assert (0x52, 0xD000) not in radio.written_blocks and (0x52, 0xF000) not in radio.written_blocks


async def test_rt950_write_image_ends_with_aprs_commit():
    image = bytes([0xFF]) * rt950pro_map.IMAGE_SIZE
    radio = FakeRadtel(image=image)
    target = bytearray(image)
    target[0] = 0x25
    got = await _write_and_compare(RT950Pro(), radio, bytes(target))
    assert got == bytes(target)
    assert radio.written_blocks[-1] == (0x54, 0x0000)


# --- CLI ---------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("WOKITOKI_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    radio = fake_rt900(image=rt900_image())
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: radio)
    assert (
        runner.invoke(cli.app, ["alias", "add", "rt900", "--driver", "radtel-rt900", "--port", "X"]).exit_code
        == 0
    )
    result = runner.invoke(cli.app, ["--plain", "read", "rt900", "-o", str(tmp_path / "r.yaml")])
    assert result.exit_code == 0, result.output
    return tmp_path, radio


def invoke(*args, input=None):
    return runner.invoke(cli.app, ["--plain", *args], input=input)


def test_cli_write_nothing_to_do(cli_env):
    tmp, radio = cli_env
    result = invoke("write", "rt900", str(tmp / "r.yaml"))
    assert result.exit_code == 0, result.output
    assert "Nothing to write" in result.output
    assert not radio.written_blocks


def test_cli_write_dry_run_and_confirm(cli_env):
    tmp, radio = cli_env
    y = tmp / "r.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("power: low", "power: high"), encoding="utf-8")
    result = invoke("write", "rt900", str(y), "--dry-run")
    assert "~ zone 1 slot 1 PMR1: power low → high" in result.output
    assert "Dry run" in result.output and not radio.written_blocks
    result = invoke("write", "rt900", str(y), input="n\n")
    assert result.exit_code == 1 and not radio.written_blocks


def test_cli_write_backup_write_verify(cli_env):
    tmp, radio = cli_env
    y = tmp / "r.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("power: low", "power: high"), encoding="utf-8")
    result = invoke("write", "rt900", str(y), "--yes")
    assert result.exit_code == 0, result.output
    assert "Verified" in result.output
    assert RT900().decode(fake_image(radio)).zones[0].channels[0].power == "high"
    folder = tmp / "data" / "rt900"
    assert len(list(folder.glob("*_before-write.img"))) == 1
    assert len(list(folder.glob("rt900_*.yaml"))) == 1  # the verified new state
    # writing the same again: nothing to do
    assert "Nothing to write" in invoke("write", "rt900", str(y)).output


def test_cli_restore_img(cli_env):
    tmp, radio = cli_env
    original = tmp / "r.img"
    y = tmp / "r.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("power: low", "power: high"), encoding="utf-8")
    assert invoke("write", "rt900", str(y), "--yes", "--no-verify").exit_code == 0
    result = invoke("write", "rt900", str(original), "--yes", "--no-verify")
    assert result.exit_code == 0, result.output
    assert "power high → low" in result.output
    assert fake_image(radio)[:0x7D00] == original.read_bytes()[:0x7D00]


def test_cli_write_other_alias_needs_force(cli_env):
    tmp, _radio = cli_env
    y = tmp / "r.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("alias: rt900", "alias: other"), encoding="utf-8")
    result = invoke("write", "rt900", str(y))
    assert result.exit_code == 1 and "--force" in result.output


def test_cli_write_without_alias_uses_yaml_alias_folder(cli_env):
    tmp, _radio = cli_env
    y = tmp / "r.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("power: low", "power: mid"), encoding="utf-8")
    result = invoke("write", "--driver", "radtel-rt900", "--port", "X", str(y), "--yes")
    assert result.exit_code == 0, result.output
    folder = tmp / "data" / "rt900"
    assert len(list(folder.glob("rt900_*_before-write.img"))) == 1
    assert not (tmp / "data" / "radtel-rt900").exists()
