import pytest

from wokitoki.core.driver import drivers, get_driver
from wokitoki.core.transport import BleAdvert, SerialPortInfo, uuid16
from wokitoki.drivers.radtel.rt950pro import RT950Pro


def test_registry_has_builtin_rt950pro():
    assert drivers()["radtel-rt950pro"] is RT950Pro
    assert get_driver("radtel-rt950pro").transports() == {"ble"}


def test_unknown_driver():
    with pytest.raises(KeyError, match="unknown driver"):
        get_driver("nope")


@pytest.mark.parametrize(
    ("name", "uuids", "expected"),
    [
        ("walkie-talkie", [uuid16("FFE0")], 60),
        ("RT-950", [uuid16("FFE0")], 90),
        ("RT950", [], 30),
        ("Headphones", ["0000110b-0000-1000-8000-00805f9b34fb"], 0),
        (None, [], 0),
    ],
)
def test_match_ble(name, uuids, expected):
    assert RT950Pro.match_ble(BleAdvert("addr", name, -60, uuids)) == expected


def test_match_serial_without_profile():
    assert RT950Pro.match_serial(SerialPortInfo("/dev/x", "", 0x1A86, 0x7523, None)) == 0


def test_generic_cable_does_not_pick_a_radio():
    from wokitoki.drivers.radtel.rt900 import RT900

    cable = SerialPortInfo("/dev/cu.usbserial-11240", "USB Serial", 0x1A86, 0x7523, None)
    assert cable.cable_chip == "CH340"
    assert RT900.match_serial(cable) == 0
    assert SerialPortInfo("/dev/cu.debug-console", "", None, None, None).cable_chip is None
