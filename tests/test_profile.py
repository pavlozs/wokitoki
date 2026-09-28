import pytest
from fakes import FakeUVK5
from test_uvk5 import image as k5_image
from typer.testing import CliRunner

from wokitoki import cli
from wokitoki.core import codeplug
from wokitoki.core.profile import ProfileError, build, get_profile, profiles
from wokitoki.drivers.quansheng.uvk5 import UVK5F4HWN
from wokitoki.drivers.radtel.rt900 import RT900
from wokitoki.drivers.radtel.rt950pro import RT950Pro


def base(driver) -> bytes:
    return bytes(k5_image()) if isinstance(driver, UVK5F4HWN) else bytes([0xFF]) * driver.image_size


def apply(driver, pid=None, **kw) -> tuple[bytes, bytes]:
    img = base(driver)
    caps = driver.capabilities
    cp = build(get_profile(pid or f"cz/{driver.id}"), driver.decode(img), fm_slots=caps.fm_slots,
               fm_name_length=caps.fm_name_length, rx_only_extra=caps.rx_only_extra, **kw)  # fmt: skip
    target = driver.encode(cp, img)
    driver.check_write(img, target)
    return img, target


def can_transmit(ch) -> bool:
    return ch.tx is not None and not ch.extra.get("tx_lock")


def test_every_builtin_profile_has_a_driver():
    found = profiles()
    assert set(found) == {"cz/radtel-rt950pro", "cz/radtel-rt900", "cz/quansheng-uvk5-f4hwn"}


@pytest.mark.parametrize("driver", [RT950Pro(), RT900(), UVK5F4HWN()], ids=lambda d: d.id)
def test_profile_builds_and_encodes(driver):
    _img, target = apply(driver, fm_library="cz/fm-ostrava" if driver.capabilities.fm_slots else None)
    cp = driver.decode(target)
    assert cp.channel_count == 16 + 14 + 40 + 40 + 3
    chans = {(z.number, c.slot): c for z in cp.zones for c in z.channels}
    if driver.capabilities.zones:
        pmr, cb, shared_5w = chans[(1, 1)], chans[(3, 1)], chans[(2, 1)]
    else:
        pmr, cb, shared_5w = chans[(1, 1)], chans[(1, 41)], chans[(1, 21)]
    assert (pmr.name, pmr.rx, pmr.bw) == ("PMR 01", 446_006_250, "narrow")
    assert shared_5w.power == {"radtel-rt950pro": "mid", "radtel-rt900": "mid"}.get(driver.id, "high")
    # PMR446 (0.5 W ERP + fixed antenna), CB and amateur channels: receive only by default
    assert not can_transmit(pmr) and not can_transmit(cb) and can_transmit(shared_5w)
    if isinstance(driver, UVK5F4HWN):
        assert pmr.extra["tx_lock"] and pmr.tx == pmr.rx and pmr.power == "low4"
    else:
        assert pmr.tx is None and cb.tx is None and not cb.scan
    fm = cp.extra.get("broadcast").values["fm"] if "broadcast" in cp.extra else []
    assert len(fm) == driver.capabilities.fm_slots
    if driver.capabilities.fm_name_length:
        assert {"n": 3, "freq": 91.0, "name": "Frekvence 1"} in fm
    else:
        assert all("name" not in e for e in fm)


def test_merge_keeps_other_channels():
    driver = RT900()
    _img, first = apply(driver)
    cp = driver.decode(first)
    cp.zones[0].channels.append(
        codeplug.parse_channel({"slot": 500, "name": "MINE", "rx": 145.5, "power": "low"}, "t")
    )
    img2 = driver.encode(cp, first)
    prof = get_profile("cz/radtel-rt900")
    kept = build(prof, driver.decode(img2), merge=True)
    replaced = build(prof, driver.decode(img2))
    assert any(c.slot == 500 for c in kept.zones[0].channels)
    assert not any(c.slot == 500 for c in replaced.zones[0].channels)


def test_profile_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path))
    user = tmp_path / "profiles" / "xx"
    user.mkdir(parents=True)
    (user / "radtel-rt900.yaml").write_text(
        "driver: radtel-rt900\nblocks:\n  - {start: 995, library: cz/pmr446}\nsettings: {turbo: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="do not fit"):
        apply(RT900(), "xx/radtel-rt900")
    (user / "radtel-rt900.yaml").write_text("driver: radtel-rt900\nsettings: {turbo: 1}\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="no setting 'turbo'"):
        apply(RT900(), "xx/radtel-rt900")
    with pytest.raises(ProfileError, match="no FM broadcast memories"):
        apply(RT900(), fm_library="cz/fm-praha")


def test_cli_profile_apply_and_build(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("WOKITOKI_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    radio = FakeUVK5(bytes(k5_image()))
    monkeypatch.setattr(cli, "make_transport", lambda driver, kind, target: radio)
    runner = CliRunner()
    assert (
        runner.invoke(
            cli.app, ["alias", "add", "k5", "--driver", "quansheng-uvk5-f4hwn", "--port", "X"]
        ).exit_code
        == 0
    )
    result = runner.invoke(
        cli.app, ["--plain", "profile", "apply", "k5", "--fm", "cz/fm-ostrava", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "+ zone 1 slot 41: new" in result.output and "broadcast.fm[1]" in result.output
    assert bytes(radio.eeprom) == bytes(k5_image())  # dry run
    result = runner.invoke(cli.app, ["--plain", "profile", "apply", "k5", "--yes"])
    assert result.exit_code == 0, result.output
    assert UVK5F4HWN().decode(bytes(radio.eeprom)).channel_count == 113
    img = tmp_path / "k5.img"
    img.write_bytes(bytes(k5_image()))
    out = tmp_path / "k5.yaml"
    result = runner.invoke(
        cli.app, ["--plain", "profile", "build", str(img), "-o", str(out), "--fm", "cz/fm-brno"]
    )
    assert result.exit_code == 0, result.output
    assert codeplug.load(out).channel_count == 113


@pytest.mark.parametrize("driver", [RT900(), UVK5F4HWN()], ids=lambda d: d.id)
@pytest.mark.parametrize(
    ("kw", "pmr", "shared", "cb", "warnings"),
    [
        ({}, False, True, False, 0),
        ({"tx": "off"}, False, False, False, 0),
        ({"tx": "on"}, True, True, True, 4),  # PMR, 2× CB against the profile, amateur (licence)
        ({"tx_on": frozenset({"cz/pmr446"})}, True, True, False, 1),
    ],
)
def test_tx_switches(driver, kw, pmr, shared, cb, warnings):
    notes: list[str] = []
    _img, target = apply(driver, warnings=notes, **kw)
    ch = {c.slot: c for z in driver.decode(target).zones for c in z.channels}
    assert (can_transmit(ch[1]), can_transmit(ch[21]), can_transmit(ch[41])) == (pmr, shared, cb)
    assert len(notes) == warnings
    if warnings:
        assert all("own responsibility" in n for n in notes)


def test_tx_on_never_for_receive_only_libraries(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path))
    (tmp_path / "library" / "xx").mkdir(parents=True)
    (tmp_path / "library" / "xx" / "air.yaml").write_text(
        "legal: {tx: rx-only}\ndefaults: {mode: AM, power: low}\nchannels:\n  - {name: TWR, rx: 120.805}\n",
        encoding="utf-8",
    )
    (tmp_path / "profiles" / "xx").mkdir(parents=True)
    (tmp_path / "profiles" / "xx" / "radtel-rt900.yaml").write_text(
        "driver: radtel-rt900\nblocks:\n  - {start: 1, library: xx/air}\n", encoding="utf-8"
    )
    _img, target = apply(RT900(), "xx/radtel-rt900", tx="on")  # global on skips receive-only libraries
    assert not can_transmit(RT900().decode(target).zones[0].channels[0])
    with pytest.raises(ProfileError, match="receive only"):
        apply(RT900(), "xx/radtel-rt900", tx_on=frozenset({"xx/air"}))
    with pytest.raises(ProfileError, match="does not use cz/pmr446"):
        apply(RT900(), "xx/radtel-rt900", tx_on=frozenset({"cz/pmr446"}))


def test_cli_tx_on_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path / "config"))
    img = tmp_path / "k5.img"
    img.write_bytes(bytes(k5_image()))
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["--plain", "profile", "build", str(img), "-o", str(tmp_path / "k5.yaml"), "--tx-on", "cz/pmr446"],
    )
    assert result.exit_code == 0, result.output
    assert "Warning: TX is ON for cz/pmr446 (restricted) – at your own responsibility" in result.output
    pmr = codeplug.load(tmp_path / "k5.yaml").zones[0].channels[0]
    assert not pmr.extra.get("tx_lock")
