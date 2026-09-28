import pytest
from typer.testing import CliRunner

from wokitoki import cli
from wokitoki.core.library import LibraryError, get_library, libraries


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WOKITOKI_CONFIG", str(tmp_path / "config"))
    return tmp_path


def test_builtin_libraries_load():
    libs = libraries()
    assert {"cz/pmr446", "cz/cb-cept40", "cz/cb-41-80", "cz/shared", "cz/amateur-calling"} <= set(libs)
    pmr = libs["cz/pmr446"]
    assert pmr.builtin and pmr.tx == "restricted" and len(pmr.channels) == 16
    # defaults are applied to every channel
    assert pmr.channels[0] == {
        "name": "PMR 01", "rx": 446.00625, "mode": "FM", "bw": "narrow", "power": "low",
        "rx_tone": "off", "tx_tone": "off", "scan": True,
    }  # fmt: skip


def test_user_library_overrides_builtin(isolated):
    user = isolated / "config" / "library" / "cz"
    user.mkdir(parents=True)
    (user / "pmr446.yaml").write_text(
        'library: cz/pmr446\ntitle: "Mine"\nchannels:\n  - {name: "X", rx: 446.1}\n', encoding="utf-8"
    )
    lib = get_library("cz/pmr446")
    assert (lib.title, lib.builtin, len(lib.channels)) == ("Mine", False, 1)


def test_invalid_user_library(isolated):
    user = isolated / "config" / "library" / "my"
    user.mkdir(parents=True)
    (user / "bad.yaml").write_text("channels:\n  - {name: X, rx: 1, freq: 2}\n", encoding="utf-8")
    with pytest.raises(LibraryError, match="unknown keys freq"):
        libraries()


def test_unknown_library():
    with pytest.raises(LibraryError, match="unknown library"):
        get_library("cz/nope")


def test_cli_lib_list_and_show():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--plain", "lib", "list"])
    assert result.exit_code == 0, result.output
    assert "cz/pmr446\tPMR446 (16 channels)\t16 channels\trestricted\tbuilt-in" in result.output
    result = runner.invoke(cli.app, ["--plain", "lib", "show", "cz/cb-cept40"])
    assert result.exit_code == 0, result.output
    assert "1\tCB 01\t26.965\t\tFM\tnarrow\tmid\t\tyes" in result.output
    assert runner.invoke(cli.app, ["lib", "show", "cz/nope"]).exit_code == 1


def test_fm_libraries_for_all_regions():
    libs = libraries()
    fm = {k: v for k, v in libs.items() if v.kind == "broadcast"}
    assert len(fm) == 13 and "cz/fm-ostrava" in fm
    for lib in fm.values():
        assert lib.band == "fm" and lib.tx == "rx-only" and lib.stations
        freqs = [s["freq"] for s in lib.stations]
        assert len(freqs) == len(set(freqs))  # one programme per frequency
        assert all(len(s["name"]) <= 12 and s["name"].isascii() for s in lib.stations)
    ostrava = {s["freq"]: s["name"] for s in fm["cz/fm-ostrava"].stations}
    assert ostrava[91] == "Frekvence 1" and ostrava[101.4] == "Radiozurnal"


def test_invalid_broadcast_library(isolated):
    user = isolated / "config" / "library" / "my"
    user.mkdir(parents=True)
    (user / "fm.yaml").write_text(
        "kind: broadcast\nstations:\n  - {name: X, freq: 150.0}\n", encoding="utf-8"
    )
    with pytest.raises(LibraryError, match="outside the fm band"):
        libraries()
