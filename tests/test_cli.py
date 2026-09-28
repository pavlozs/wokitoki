import pytest
from fakes import FakeRadtel
from typer.testing import CliRunner

from wokitoki import cli
from wokitoki.core.transport import SerialPortInfo

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("WOKITOKI_HOME", str(tmp_path / "data"))
    return tmp_path


def invoke(*args, input=None):
    return runner.invoke(cli.app, ["--plain", *args], input=input)


def test_radios():
    result = invoke("radios")
    assert result.exit_code == 0
    assert "radtel-rt950pro" in result.output


def test_paths_respect_env(isolated):
    result = invoke("paths")
    assert str(isolated / "config" / "aliases.yaml") in result.output
    assert str(isolated / "data") in result.output


def test_alias_lifecycle(isolated):
    result = invoke(
        "alias", "add", "rt950", "--driver", "radtel-rt950pro", "--ble", "AA-BB", "--note", "modrá"
    )
    assert result.exit_code == 0, result.output
    assert (isolated / "config" / "aliases.yaml").read_text(encoding="utf-8").count("rt950") == 2

    result = invoke("alias", "list")
    assert "rt950\tradtel-rt950pro\tble AA-BB\tmodrá" in result.output

    assert invoke("alias", "rename", "rt950", "modra").exit_code == 0
    result = invoke("alias", "show", "modra")
    assert "AA-BB" in result.output

    assert invoke("alias", "rm", "modra", "--yes").exit_code == 0
    assert "No aliases" in invoke("alias", "list").output


def test_alias_add_rejects_unsupported_transport():
    result = invoke("alias", "add", "x", "--driver", "radtel-rt950pro", "--port", "COM3")
    assert result.exit_code == 1
    assert "does not support" in result.output


def test_alias_add_unknown_driver():
    result = invoke("alias", "add", "x", "--driver", "nope", "--ble", "A")
    assert result.exit_code == 1
    assert "unknown driver" in result.output


def test_ping_via_alias(monkeypatch):
    invoke("alias", "add", "rt950", "--driver", "radtel-rt950pro", "--ble", "AA-BB")
    radio = FakeRadtel()
    seen = {}

    def fake_transport(driver, kind, target):
        seen.update(driver=driver.id, kind=kind, target=target)
        return radio

    monkeypatch.setattr(cli, "make_transport", fake_transport)
    result = invoke("ping", "rt950")
    assert result.exit_code == 0, result.output
    assert "RT-950" in result.output
    assert seen == {"driver": "radtel-rt950pro", "kind": "ble", "target": "AA-BB"}
    assert radio.opened and radio.closed


def test_ping_needs_target():
    result = invoke("ping")
    assert result.exit_code == 1
    assert "alias" in result.output


def test_ping_unknown_alias():
    result = invoke("ping", "nic")
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_read_saves_yaml_and_backup(isolated, monkeypatch):
    from wokitoki.drivers.radtel.rt950pro_map import IMAGE_SIZE

    invoke("alias", "add", "rt950", "--driver", "radtel-rt950pro", "--ble", "AA-BB")
    image = bytes([0xFF]) * IMAGE_SIZE
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: FakeRadtel(image=image))
    result = invoke("read", "rt950")
    assert result.exit_code == 0, result.output
    files = sorted((isolated / "data" / "rt950").iterdir())
    assert [f.suffix for f in files] == [".img", ".yaml"]
    assert files[0].read_bytes() == image
    text = files[1].read_text(encoding="utf-8")
    assert "alias: rt950" in text and "model: RT-950" in text
    assert f"image: {files[0].name}" in text


def test_decode_detects_driver_by_size(isolated):
    from wokitoki.drivers.radtel.rt950pro_map import IMAGE_SIZE

    img = isolated / "backup.img"
    img.write_bytes(bytes([0xFF]) * IMAGE_SIZE)
    result = invoke("decode", str(img))
    assert result.exit_code == 0, result.output
    assert "driver: radtel-rt950pro" in (isolated / "backup.yaml").read_text(encoding="utf-8")
    assert invoke("decode", str(img)).exit_code == 1  # no overwrite without --force


def test_decode_chirp_img_with_metadata(isolated):
    from wokitoki.cli import CHIRP_IMG_MAGIC
    from wokitoki.drivers.radtel.rt900_map import IMAGE_SIZE

    img = isolated / "chirp.img"
    img.write_bytes(bytes([0xFF]) * IMAGE_SIZE + CHIRP_IMG_MAGIC + b"eyJyYWRpbyI6ICJSVC05MDBfQlQifQ==")
    result = invoke("decode", str(img))
    assert result.exit_code == 0, result.output
    assert "driver: radtel-rt900" in (isolated / "chirp.yaml").read_text(encoding="utf-8")


CABLE = SerialPortInfo("/dev/cu.usbserial-1", "USB Serial", 0x1A86, 0x7523, None)


def test_scan_identifies_radio_behind_cable(monkeypatch):
    from fakes import fake_rt900

    monkeypatch.setattr(cli, "list_serial_ports", lambda: [CABLE])
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: fake_rt900())
    result = invoke("scan", "--serial")
    assert result.exit_code == 0, result.output
    assert "RT-900 via CH340 cable\t/dev/cu.usbserial-1\t\tradtel-rt900 (identified)" in result.output


def test_scan_cable_without_answer_stays_unknown(monkeypatch):
    monkeypatch.setattr(cli, "list_serial_ports", lambda: [CABLE])
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: FakeRadtel())  # wrong magic
    result = invoke("scan", "--serial")
    assert "USB-serial cable (CH340)\t/dev/cu.usbserial-1\t\t?" in result.output
    assert "does not tell which radio" in result.output


def test_scan_no_probe_does_not_touch_the_port(monkeypatch):
    monkeypatch.setattr(cli, "list_serial_ports", lambda: [CABLE])

    def forbidden(*_args):
        raise AssertionError("port must not be opened")

    monkeypatch.setattr(cli, "make_transport", forbidden)
    result = invoke("scan", "--serial", "--no-probe")
    assert result.exit_code == 0, result.output


def test_ping_driver_auto_finds_the_radio(monkeypatch):
    # an RT-950 Pro: the RT-900 handshake gets no answer, the RT-950 one does
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: FakeRadtel())
    result = invoke("ping", "--driver", "auto", "--ble", "AA-BB")
    assert result.exit_code == 0, result.output
    assert "Identified as radtel-rt950pro" in result.output and "RT-950" in result.output


def test_ping_driver_auto_without_answer(monkeypatch):
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: FakeRadtel(magic=b"NOTHING"))
    result = invoke("ping", "--driver", "auto", "--ble", "AA-BB")
    assert result.exit_code == 1 and "no driver got an answer" in result.output


def test_scan_save_identifies_an_uncertain_ble_radio(isolated, monkeypatch):
    from fakes import fake_rt900

    from wokitoki.core.aliases import AliasStore
    from wokitoki.core.transport import BleAdvert, uuid16

    async def adverts(_timeout):
        return [BleAdvert("B2C3D4E5", "walkie-talkie", -50, [uuid16("FFE0")])]

    monkeypatch.setattr(cli, "scan_ble", adverts)
    monkeypatch.setattr(cli, "list_serial_ports", list)
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: fake_rt900())
    result = invoke("scan", "--ble", "--save", input="1\n\nmyradio\n\n")
    assert result.exit_code == 0, result.output
    assert "uncertain" in result.output  # both Radtels advertise FFE0
    assert "The radio answered as radtel-rt900" in result.output
    alias = AliasStore().get("myradio")
    assert (alias.driver, alias.transport, alias.address) == ("radtel-rt900", "ble", "B2C3D4E5")
