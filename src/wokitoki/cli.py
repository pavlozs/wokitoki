"""Command line interface (docs/CLI.md)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import shlex
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .core import codeplug, paths
from .core.aliases import Alias, AliasError, AliasStore, validate_name
from .core.codeplug import Codeplug, CodeplugError
from .core.driver import Driver, DriverError, RadioInfo, drivers, get_driver
from .core.library import LibraryError, get_library, libraries
from .core.profile import ProfileError, build, decide_tx, get_profile, profiles
from .core.transport import (
    BleAdvert,
    BleTransport,
    SerialPortInfo,
    SerialTransport,
    Transport,
    TransportError,
    list_serial_ports,
    scan_ble,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Command line programmer for handheld radios (Bluetooth LE / USB).",
)
alias_app = typer.Typer(no_args_is_help=True, help="Manage radio aliases (aliases.yaml).")
app.add_typer(alias_app, name="alias")
lib_app = typer.Typer(no_args_is_help=True, help="Libraries: channels (PMR446, CB, …) and FM stations.")
app.add_typer(lib_app, name="lib")
profile_app = typer.Typer(no_args_is_help=True, help="Default configurations per country and radio.")
app.add_typer(profile_app, name="profile")


class State:
    plain = False
    console = Console(highlight=False)
    err = Console(stderr=True, highlight=False)


state = State()


def _setup_logging(verbose: bool, log_file: Path | None) -> None:
    root = logging.getLogger("wokitoki")
    root.setLevel(logging.DEBUG if (verbose or log_file) else logging.WARNING)
    root.handlers.clear()
    root.propagate = False
    console_handler: logging.Handler
    if state.plain:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    else:
        from rich.logging import RichHandler

        console_handler = RichHandler(console=state.err, show_path=False, markup=False)
    console_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(console_handler)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"wokitoki {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("-v", "--verbose", help="Verbose log including TX/RX in hex.")
    ] = False,
    plain: Annotated[bool, typer.Option("--plain", help="No colours or tables (for scripts).")] = False,
    log_file: Annotated[Path | None, typer.Option("--log", help="Write a verbose log to a file.")] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Print the version.")
    ] = False,
) -> None:
    state.plain = plain
    state.console = Console(highlight=False, no_color=plain, force_terminal=False if plain else None)
    state.err = Console(stderr=True, highlight=False, no_color=plain)
    _setup_logging(verbose, log_file)


# --- helpers -----------------------------------------------------------------


def fail(message: str) -> typer.Exit:
    state.err.print(f"[red]Error:[/red] {message}" if not state.plain else f"Error: {message}")
    return typer.Exit(1)


@contextmanager
def errors():
    try:
        yield
    except (
        AliasError,
        CodeplugError,
        LibraryError,
        ProfileError,
        TransportError,
        DriverError,
        NotImplementedError,
    ) as e:
        raise fail(str(e)) from None
    except KeyError as e:
        raise fail(e.args[0] if e.args else str(e)) from None
    except KeyboardInterrupt:
        raise fail("interrupted") from None


def print_table(title: str | None, columns: list[str], rows: list[list[str]]) -> None:
    if state.plain:
        for row in rows:
            typer.echo("\t".join(row))
        return
    table = Table(title=title, title_justify="left")
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*row)
    state.console.print(table)


def make_transport(driver: type[Driver], kind: str, target: str) -> Transport:
    if kind == "ble":
        if driver.ble is None:
            raise DriverError(f"{driver.id} does not support Bluetooth LE")
        return BleTransport(target, driver.ble)
    if kind == "serial":
        if driver.serial is None:
            raise DriverError(f"{driver.id} does not support a serial port / USB cable")
        return SerialTransport(target, driver.serial)
    raise DriverError(f"unknown transport '{kind}'")


def resolve_radio(
    radio: str | None, driver_id: str | None, ble: str | None, port: str | None
) -> tuple[type[Driver], str, str, str | None]:
    """Alias, or explicit --driver with --ble/--port → (driver, kind, target, alias name)."""
    if radio:
        if driver_id or ble or port:
            raise AliasError("give either an alias or --driver with --ble/--port, not both")
        alias = AliasStore().get(radio)
        return get_driver(alias.driver), alias.transport, alias.target, alias.name
    if not driver_id or bool(ble) == bool(port):
        raise AliasError("give an alias, or --driver and exactly one of --ble / --port")
    kind, target = ("ble", ble) if ble else ("serial", port)
    return get_driver(driver_id), kind, target, None


def _implemented(driver: type[Driver], method: str) -> bool:
    return getattr(driver, method) is not getattr(Driver, method)


def suggest_alias(driver_id: str | None, taken: set[str]) -> str:
    base = (driver_id or "radio").split("-", 1)[-1]
    base = re.sub(r"[^a-z0-9_-]", "", base.lower()) or "radio"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


# --- radios, paths -----------------------------------------------------------


@app.command()
def radios() -> None:
    """Supported radios (drivers) and what they can do."""
    rows = []
    for driver_id, driver in drivers().items():
        caps = driver.capabilities
        features = [
            name
            for name, method in (("ping", "identify"), ("read", "read_image"), ("write", "write_image"))
            if _implemented(driver, method)
        ]
        rows.append(
            [
                driver_id,
                f"{driver.vendor} {driver.model}",
                ", ".join(sorted(driver.transports())) or "-",
                (
                    f"{caps.channels} / {caps.zones}×{caps.channels_per_zone}"
                    if caps.zones
                    else f"{caps.channels} / no zones"
                )
                if caps.channels
                else "-",
                ", ".join(features) or "-",
            ]
        )
    print_table("Drivers", ["driver", "radio", "connection", "channels / zones", "can"], rows)


@app.command("paths")
def show_paths() -> None:
    """Where aliases, libraries and radio data are."""
    rows = [
        ["configuration", str(paths.config_dir())],
        ["aliases", str(paths.aliases_file())],
        ["user libraries", str(paths.user_library_dir())],
        ["built-in libraries", str(paths.builtin_library_dir())],
        ["radio data", str(paths.data_home())],
    ]
    print_kv(rows)


# --- scan --------------------------------------------------------------------


def _best(scores: list[tuple[int, type[Driver]]]) -> tuple[list[type[Driver]], int]:
    """All drivers sharing the top score (radios of one family look alike over BLE)."""
    top = max((score for score, _ in scores), default=0)
    if top <= 0:
        return [], 0
    return [d for score, d in scores if score == top], top


def _best_ble(advert: BleAdvert) -> tuple[list[type[Driver]], int]:
    return _best([(d.match_ble(advert), d) for d in drivers().values()])


def _best_serial(port: SerialPortInfo) -> tuple[list[type[Driver]], int]:
    return _best([(d.match_serial(port), d) for d in drivers().values()])


IDENTIFIED = 101  # above any advertisement score: the radio answered a handshake


def _identify(
    kind: str, target: str, candidates: list[type[Driver]] | None = None
) -> tuple[type[Driver], RadioInfo] | None:
    """Ask the device with each driver's handshake (one short attempt each);
    the first radio that answers wins.

    Read only: a handshake enters clone mode, reads the ident and leaves. Used
    only on devices that are certainly the user's (a cable, or a BLE device
    the user picked) – never on every BLE device around.
    """
    pool = candidates or list(drivers().values())
    tried = [
        d for d in pool
        if kind in d.transports() and _implemented(d, "identify")
    ]  # fmt: skip

    async def attempt(driver_cls: type[Driver]) -> RadioInfo:
        transport = make_transport(driver_cls, kind, target)
        async with transport:
            return await driver_cls().identify(transport, quick=True)

    for driver_cls in tried:
        try:
            return driver_cls, asyncio.run(attempt(driver_cls))
        except (TransportError, DriverError) as e:
            logging.getLogger("wokitoki.scan").debug("identify %s with %s: %s", target, driver_cls.id, e)
    return None


def _probe_cable(port: str) -> tuple[type[Driver], RadioInfo] | None:
    return _identify("serial", port)


def _guess(candidates: list[type[Driver]], score: int) -> str:
    if not candidates:
        return "?"
    if score >= IDENTIFIED:
        return f"{candidates[0].id} (identified)"
    if len(candidates) > 1:
        return " / ".join(d.id for d in candidates) + f" ({score} %, uncertain)"
    return f"{candidates[0].id} ({score} %)"


@app.command()
def scan(
    ble: Annotated[bool, typer.Option("--ble", help="Bluetooth LE only.")] = False,
    serial: Annotated[bool, typer.Option("--serial", help="Serial ports / USB only.")] = False,
    timeout: Annotated[float, typer.Option("--timeout", "-t", help="BLE scan duration in seconds.")] = 8.0,
    show_all: Annotated[
        bool, typer.Option("--all", "-a", help="Also list unrecognised BLE devices and non-USB ports.")
    ] = False,
    save: Annotated[
        bool, typer.Option("--save", "-s", help="Offer to save a found radio as an alias.")
    ] = False,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe/--no-probe",
            help="Identify the radio behind USB programming cables with a short read-only handshake.",
        ),
    ] = True,
) -> None:
    """Find radios over Bluetooth LE and serial ports and guess the model."""
    if not ble and not serial:
        ble = serial = True
    # kind, name, target, rssi, candidate drivers, score
    found: list[tuple[str, str, str, str, list[type[Driver]], int]] = []
    hidden = 0
    with errors():
        if ble:
            try:
                with state.err.status(f"Scanning for Bluetooth LE devices ({timeout:g} s)…"):
                    adverts = asyncio.run(scan_ble(timeout))
            except TransportError as e:
                state.err.print(f"Bluetooth unavailable: {e}")
                adverts = []
            for advert in adverts:
                candidates, score = _best_ble(advert)
                if not candidates and not show_all:
                    hidden += 1
                    continue
                rssi = str(advert.rssi) if advert.rssi is not None else ""
                found.append(("ble", advert.name or "", advert.address, rssi, candidates, score))
        if serial:
            for port in list_serial_ports():
                candidates, score = _best_serial(port)
                # Programming cables are USB; skip Bluetooth/debug pseudo ports.
                if port.vid is None and not candidates and not show_all:
                    hidden += 1
                    continue
                name = f"USB-serial cable ({port.cable_chip})" if port.cable_chip else port.description
                if probe and port.cable_chip and not candidates:
                    with state.err.status(f"Probing {port.port} …"):
                        hit = _probe_cable(port.port)
                    if hit:
                        driver, info = hit
                        candidates, score = [driver], IDENTIFIED
                        name = f"{info.model} via {port.cable_chip} cable"
                found.append(("serial", name, port.port, "", candidates, score))

    found.sort(key=lambda f: (-f[5], f[0]))
    rows = [
        [str(i), kind, name, target, rssi, _guess(candidates, score)]
        for i, (kind, name, target, rssi, candidates, score) in enumerate(found, 1)
    ]
    if rows:
        print_table("Devices found", ["#", "connection", "name", "address / port", "RSSI", "guess"], rows)
    else:
        state.console.print("Nothing found.")
    if hidden:
        state.err.print(f"({hidden} more devices/ports do not look like a radio – show them with --all)")
    if any(
        kind == "serial" and not cands and n.startswith("USB-serial cable")
        for kind, n, _t, _r, cands, _s in found
    ):
        state.err.print(
            "A cable does not tell which radio is connected – check with: "
            "wokitoki ping --driver <driver> --port <port>"
        )
    if save and found:
        _save_from_scan(found)


def _save_from_scan(found) -> None:
    choice = typer.prompt("Save as alias – device number (empty = none)", default="", show_default=False)
    if not choice.strip():
        return
    try:
        index = int(choice)
    except ValueError:
        raise fail(f"invalid choice '{choice}'") from None
    if not 1 <= index <= len(found):
        raise fail(f"invalid choice '{choice}' (1–{len(found)})")
    kind, name, target, _rssi, candidates, _score = found[index - 1]
    with errors():
        store = AliasStore()
        if len(candidates) != 1:
            # Uncertain guess: ask the device the user picked (it is theirs).
            with state.err.status(f"Identifying {target} …"):
                hit = _identify(kind, target, candidates or None)
            if hit:
                candidates = [hit[0]]
                state.console.print(f"The radio answered as {hit[0].id} ({hit[1].model}).")
            else:
                state.console.print(
                    f"No driver got an answer (radio off or connected elsewhere?). Drivers: {', '.join(drivers())}"
                )
        driver_id = typer.prompt("Driver", default=candidates[0].id if len(candidates) == 1 else None)
        get_driver(driver_id)
        alias_name = validate_name(
            typer.prompt("Alias name", default=suggest_alias(driver_id, set(store.names())))
        )
        note = typer.prompt("Note", default="", show_default=False) or None
        alias = Alias(
            name=alias_name,
            driver=driver_id,
            transport=kind,
            address=target if kind == "ble" else None,
            port=target if kind == "serial" else None,
            device_name=name or None,
            note=note,
        )
        store.add(alias)
        store.save()
    state.console.print(f"Alias [bold]{alias_name}[/bold] saved ({store.path}).")


# --- ping --------------------------------------------------------------------


@app.command()
def ping(
    radio: Annotated[str | None, typer.Argument(help="Radio alias.")] = None,
    driver_id: Annotated[
        str | None, typer.Option("--driver", "-d", help="Driver, or 'auto' (without an alias).")
    ] = None,
    ble: Annotated[str | None, typer.Option("--ble", help="BLE address (without an alias).")] = None,
    port: Annotated[str | None, typer.Option("--port", help="Serial port (without an alias).")] = None,
) -> None:
    """Connect to the radio, do the handshake and print the model.

    --driver auto tries every driver that supports the connection."""
    if driver_id == "auto":
        if radio or bool(ble) == bool(port):
            raise fail("--driver auto needs exactly one of --ble / --port (and no alias)")
        kind, target = ("ble", ble) if ble else ("serial", port)
        with errors(), state.err.status(f"Identifying {target} …"):
            hit = _identify(kind, target)
        if not hit:
            raise fail(f"no driver got an answer from {target} (radio off, busy or not supported)")
        state.console.print(f"Identified as {hit[0].id}.")
        _print_info(hit[0], hit[1], f"{kind} {target}")  # the handshake already told us everything
        return
    with errors():
        driver_cls, kind, target, alias_name = resolve_radio(radio, driver_id, ble, port)
        driver = driver_cls()
        transport = make_transport(driver_cls, kind, target)

        async def run():
            async with transport:
                return await driver.identify(transport)

        label = alias_name or target
        with state.err.status(f"Connecting to {label} ({transport.description})…"):
            info = asyncio.run(run())

    _print_info(driver_cls, info, transport.description)


def _print_info(driver_cls: type[Driver], info: RadioInfo, connection: str) -> None:
    rows = [["radio", info.model], ["driver", driver_cls.id], ["connection", connection]]
    if info.firmware:
        rows.append(["firmware", info.firmware])
    rows += [[k, str(v)] for k, v in info.details.items()]
    print_kv(rows)


def print_kv(rows: list[list[str]]) -> None:
    if state.plain:
        print_table(None, ["", ""], rows)
        return
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        state.console.print(f"[dim]{key:<{width}}[/dim]  {value}", soft_wrap=True)


# --- read / decode -----------------------------------------------------------


def _free_path(path: Path) -> Path:
    """``name.ext`` → ``name_2.ext`` … when the file already exists."""
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return candidate


def _save_codeplug(
    driver: Driver,
    image: bytes,
    yaml_path: Path,
    img_path: Path,
    *,
    alias: str | None,
    model: str | None,
    read_at: str | None,
    notes: list[str] | None = None,
) -> Codeplug:
    cp = driver.decode(image)
    cp.alias = alias
    cp.model = model
    cp.read_at = read_at
    if notes is not None:
        cp.notes = notes
    try:
        cp.image = Path(os.path.relpath(img_path, yaml_path.parent)).as_posix()
    except ValueError:  # Windows: another drive – no relative path exists
        cp.image = img_path.resolve().as_posix()
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    codeplug.save(cp, yaml_path)
    return cp


def _summary(cp: Codeplug, yaml_path: Path, img_path: Path) -> None:
    used = [f"{z.number}: {len(z.channels)}" for z in cp.zones if z.channels]
    rows = [
        ["channels", f"{cp.channel_count} (zones {', '.join(used) or '-'})"],
        ["YAML", str(yaml_path)],
        ["backup", str(img_path)],
    ]
    if cp.model:
        rows.insert(0, ["radio", cp.model])
    print_kv(rows)


@app.command()
def read(
    radio: Annotated[str | None, typer.Argument(help="Radio alias.")] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Target YAML (the .img is stored next to it).")
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing -o file.")] = False,
    driver_id: Annotated[
        str | None, typer.Option("--driver", "-d", help="Driver (without an alias).")
    ] = None,
    ble: Annotated[str | None, typer.Option("--ble", help="BLE address (without an alias).")] = None,
    port: Annotated[str | None, typer.Option("--port", help="Serial port (without an alias).")] = None,
) -> None:
    """Download the complete configuration from the radio → YAML + .img byte backup."""
    with errors():
        driver_cls, kind, target, alias_name = resolve_radio(radio, driver_id, ble, port)
        driver = driver_cls()
        transport = make_transport(driver_cls, kind, target)
        now = dt.datetime.now().astimezone()
        if output:
            if output.suffix.lower() == ".img":
                raise DriverError("-o names the YAML file (the .img backup is stored next to it)")
            yaml_path = output
            img_path = output.with_suffix(".img")
            existing = [p for p in (yaml_path, img_path) if p.exists()]
            if existing and not force:
                raise DriverError(f"{existing[0]} already exists (overwrite: --force)")
        else:
            name = alias_name or driver_cls.id
            base = paths.radio_dir(name) / f"{name}_{now:%Y-%m-%d_%H%M}.yaml"
            yaml_path = _free_path(base)
            img_path = _free_path(yaml_path.with_suffix(".img"))

        image = asyncio.run(_read_with_progress(driver, transport, alias_name or target))

        # The raw backup matters most – store it before anything can fail.
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(image)
        try:
            cp = _save_codeplug(
                driver,
                image,
                yaml_path,
                img_path,
                alias=alias_name,
                model=driver.info.model if driver.info else None,
                read_at=now.isoformat(timespec="seconds"),
            )
        except NotImplementedError:
            state.console.print(f"Backup saved: {img_path}")
            raise
    _summary(cp, yaml_path, img_path)


async def _with_progress(transport: Transport, label: str, what: str, action) -> Any:
    """Run ``action(transport, progress)`` inside the connection, with a progress bar."""
    if state.plain:
        async with transport:
            return await action(transport, None)

    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=state.err,
        transient=True,
    ) as bar:
        task = bar.add_task(f"Connecting to {label}…", total=None)
        async with transport:
            bar.update(task, description="Handshake…")

            def progress(done: int, total: int) -> None:
                bar.update(task, description=what, completed=done, total=total)

            return await action(transport, progress)


async def _read_with_progress(driver: Driver, transport: Transport, label: str) -> bytes:
    return await _with_progress(transport, label, "Reading memory", driver.read_image)


CHIRP_IMG_MAGIC = b"\x00\xffchirp\xeeimg\x00\x01"


@app.command()
def decode(
    image_file: Annotated[Path, typer.Argument(help=".img byte backup.", exists=True, dir_okay=False)],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Target YAML (default: next to the .img).")
    ] = None,
    driver_id: Annotated[
        str | None, typer.Option("--driver", "-d", help="Driver (default: by file size).")
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing YAML.")] = False,
) -> None:
    """Convert an .img backup (wokitoki, RT950Pro or CHIRP) to YAML (no radio needed)."""
    with errors():
        image = image_file.read_bytes()
        image = _strip_chirp(image)
        driver_cls = get_driver(driver_id) if driver_id else _driver_by_size(image)
        yaml_path = output or image_file.with_suffix(".yaml")
        if yaml_path.suffix.lower() == ".img" or yaml_path.resolve() == image_file.resolve():
            raise DriverError("-o names the YAML file – it cannot replace the .img backup")
        if yaml_path.exists() and not force:
            raise DriverError(f"{yaml_path} already exists (overwrite: --force)")
        cp = _save_codeplug(driver_cls(), image, yaml_path, image_file, alias=None, model=None, read_at=None)
    _summary(cp, yaml_path, image_file)


def _driver_by_size(image: bytes) -> type[Driver]:
    matching = [d for d in drivers().values() if d.image_size == len(image)]
    if len(matching) != 1:
        names = ", ".join(d.id for d in matching) or "none"
        raise DriverError(f"cannot pick a driver by size {len(image)} B ({names}) – use --driver")
    return matching[0]


def _strip_chirp(image: bytes) -> bytes:
    """CHIRP appends metadata after the raw memory map."""
    marker = image.find(CHIRP_IMG_MAGIC)
    return image[:marker] if marker >= 0 else image


def _restore_target(driver: Driver, current: bytes, backup: bytes) -> bytes:
    """Restoring an .img only ever touches the writable areas; the rest (calibration,
    areas the radio maintains itself) is taken from the radio as it is now."""
    driver.check_image(backup)
    target = bytearray(current)
    for lo, hi in driver.writable_ranges():
        target[lo:hi] = backup[lo:hi]
    return bytes(target)


def _stamp() -> str:
    return f"{dt.datetime.now().astimezone():%Y-%m-%d_%H%M}"


@app.command()
def write(
    radio: Annotated[str | None, typer.Argument(help="Radio alias.")] = None,
    file: Annotated[
        Path | None, typer.Argument(help="Configuration YAML, or an .img backup to restore.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Only show what would change.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Write a configuration read from another alias.")
    ] = False,
    verify: Annotated[
        bool, typer.Option("--verify/--no-verify", help="Read the radio back after writing and compare.")
    ] = True,
    driver_id: Annotated[
        str | None, typer.Option("--driver", "-d", help="Driver (without an alias).")
    ] = None,
    ble: Annotated[str | None, typer.Option("--ble", help="BLE address (without an alias).")] = None,
    port: Annotated[str | None, typer.Option("--port", help="Serial port (without an alias).")] = None,
) -> None:
    """Write a configuration (YAML) or restore a backup (.img) to the radio.

    Reads the radio first, shows exactly what will change and asks before
    writing; the radio's previous state is saved as a backup .img."""
    if file is None and radio and Path(radio).suffix.lower() in (".yaml", ".yml", ".img"):
        radio, file = None, Path(radio)  # wokitoki write --port … file.yaml
    if file is None:
        raise fail("give the configuration file: wokitoki write <alias> <file.yaml | backup.img>")
    if not file.is_file():
        raise fail(f"{file} does not exist")
    with errors():
        driver_cls, kind, target_addr, alias_name = resolve_radio(radio, driver_id, ble, port)
        is_image = file.suffix.lower() == ".img"
        cp = None
        if is_image:
            backup = _strip_chirp(file.read_bytes())
            driver_cls().check_image(backup)
        else:
            cp = codeplug.load(file)
            if cp.driver != driver_cls.id:
                raise DriverError(f"{file} is for {cp.driver}, the radio uses {driver_cls.id}")
            if cp.alias and alias_name and cp.alias != alias_name and not force:
                raise DriverError(
                    f"{file} was read from '{cp.alias}', not '{alias_name}' – use --force if that is intended"
                )
            if cp.alias:
                validate_name(cp.alias)  # it names the folder for backups

    def make_target(driver: Driver, current: bytes) -> bytes:
        if is_image:
            return _restore_target(driver, current, backup)
        if (base_path := cp.image_path()) and base_path.exists():
            base = base_path.read_bytes()
            if len(base) == len(current) and codeplug.diff(driver.decode(base), driver.decode(current)):
                state.err.print(
                    f"Note: the radio changed since {base_path.name} was read – the list below "
                    "also reverts those changes."
                )
        return driver.encode(cp, current)

    # Without an alias (--port/--ble) the files go where the YAML came from.
    name = alias_name or (cp.alias if cp else None) or driver_cls.id
    _write_flow(
        driver_cls, kind, target_addr, alias_name, name, make_target, dry_run=dry_run, yes=yes, verify=verify
    )


def _write_flow(
    driver_cls: type[Driver],
    kind: str,
    target_addr: str,
    alias_name: str | None,
    name: str,
    make_target,
    *,
    dry_run: bool,
    yes: bool,
    verify: bool,
    save_yaml: Path | None = None,
) -> None:
    """Read the radio → make_target(driver, current) → check → diff → confirm →
    backup → write → verify. ``save_yaml``: also store the target as YAML."""
    driver = driver_cls()
    label = alias_name or target_addr
    with errors():
        current = asyncio.run(
            _read_with_progress(driver, make_transport(driver_cls, kind, target_addr), label)
        )
        model = driver.info.model if driver.info else None
        target = make_target(driver, current)
        changed = driver.check_write(current, target)
        lines = codeplug.diff(driver.decode(current), driver.decode(target))
        if save_yaml:
            folder = paths.radio_dir(name)
            folder.mkdir(parents=True, exist_ok=True)
            base = _free_path(folder / f"{name}_{_stamp()}_before-profile.img")
            base.write_bytes(current)
            _save_codeplug(driver, target, save_yaml, base, alias=alias_name, model=model, read_at=None)
            state.console.print(f"Configuration saved: {save_yaml} (base: {base.name})")

    if not changed:
        state.console.print("Nothing to write – the radio already holds this configuration.")
        return
    state.console.print(f"Changes to write to {label} ({model or driver_cls.model}):")
    for line in lines:
        state.console.print(f"  {line}", markup=False)
    if not lines:
        state.console.print("  (only bytes that the YAML does not show)")
    state.console.print(f"{len(changed)} bytes change.")
    if dry_run:
        state.console.print("Dry run – nothing written.")
        return
    if not yes and not typer.confirm("Write these changes to the radio?"):
        raise typer.Exit(1)

    with errors():
        folder = paths.radio_dir(name)
        folder.mkdir(parents=True, exist_ok=True)
        backup_path = _free_path(folder / f"{name}_{_stamp()}_before-write.img")
        backup_path.write_bytes(current)
        state.console.print(f"Backup of the radio saved: {backup_path}")
        try:
            asyncio.run(
                _with_progress(
                    make_transport(driver_cls, kind, target_addr),
                    label,
                    "Writing memory",
                    lambda t, progress: driver.write_image(t, target, progress),
                )
            )
        except (TransportError, DriverError) as e:
            raise DriverError(
                f"writing failed: {e}\nThe radio may be partly written. Restore it with: "
                f"wokitoki write {alias_name or '<alias>'} {backup_path}"
            ) from e
        state.console.print("Written.")
        if verify:
            _verify_write(driver_cls, kind, target_addr, label, target, name)


def _verify_write(driver_cls, kind: str, target_addr: str, label: str, target: bytes, name: str) -> None:
    """Read the radio back (it may restart or drop the link after writing)."""
    import time

    driver = driver_cls()
    last: Exception | None = None
    for attempt in range(3):
        time.sleep(3)
        try:
            readback = asyncio.run(
                _read_with_progress(driver, make_transport(driver_cls, kind, target_addr), label)
            )
            break
        except (TransportError, DriverError) as e:
            last = e
            state.err.print(f"Verification read {attempt + 1}/3 failed: {e}")
    else:
        raise DriverError(f"could not read the radio back to verify ({last}) – check it with: wokitoki read")
    bad = [i for lo, hi in driver.writable_ranges() for i in range(lo, hi) if readback[i] != target[i]]
    if bad:
        raise DriverError(
            f"verification failed: {len(bad)} bytes differ from what was written (first at 0x{bad[0]:04X})"
        )
    yaml_path = _free_path(paths.radio_dir(name) / f"{name}_{_stamp()}.yaml")
    img_path = _free_path(yaml_path.with_suffix(".img"))
    img_path.write_bytes(readback)
    _save_codeplug(
        driver,
        readback,
        yaml_path,
        img_path,
        alias=None if name == driver_cls.id else name,
        model=driver.info.model if driver.info else None,
        read_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    state.console.print(f"Verified: the radio holds the new configuration. Current state saved: {yaml_path}")


# --- lib ---------------------------------------------------------------------

TX_LABELS = {
    "allowed": "yes",
    "restricted": "restricted",
    "licence": "licence only",
    "rx-only": "no (receive only)",
}


@lib_app.command("list")
def lib_list() -> None:
    """List libraries: channels (PMR, CB, …) and broadcast stations (FM)."""
    with errors():
        libs = libraries()
    rows = [
        [
            lib.id,
            lib.title,
            f"{len(lib.stations)} {lib.band} stations"
            if lib.kind == "broadcast"
            else f"{len(lib.channels)} channels",
            TX_LABELS.get(lib.tx, lib.tx),
            "built-in" if lib.builtin else "user",
        ]
        for lib in libs.values()
    ]
    print_table("Libraries", ["library", "title", "content", "transmit", "origin"], rows)
    state.err.print(f"User libraries: {paths.user_library_dir()}")


def _fmt_mhz(value) -> str:
    return "" if value is None else f"{float(value):.5f}".rstrip("0").rstrip(".")


@lib_app.command("show")
def lib_show(lib_id: Annotated[str, typer.Argument(help="Library id, e.g. cz/pmr446.")]) -> None:
    """Show a library: legal notes and channels (with defaults applied)."""
    with errors():
        lib = get_library(lib_id)
    rows = [["library", lib.id], ["title", lib.title], ["transmit", TX_LABELS.get(lib.tx, lib.tx)]]
    if "max_power_w" in lib.legal:
        rows.append(["max power", f"{lib.legal['max_power_w']} W"])
    if lib.legal.get("note"):
        rows.append(["note", " ".join(str(lib.legal["note"]).split())])
    if lib.source:
        rows.append(["source", lib.source])
    rows.append(["file", str(lib.path)])
    print_kv(rows)
    if lib.kind == "broadcast":
        station_rows = [
            [str(i), st["name"], f"{st['freq']:g}", str(st.get("program", "")), str(st.get("transmitter", "")),
             f"{st['erp_kw']:g}" if "erp_kw" in st else "", f"{st['distance_km']:g}" if "distance_km" in st else ""]
            for i, st in enumerate(lib.stations, 1)
        ]  # fmt: skip
        print_table(None, ["#", "name", "MHz", "programme", "transmitter", "ERP kW", "km"], station_rows)
        return
    tone = lambda v: "" if v in (None, "off", False) else str(v)
    channel_rows = [
        [
            str(i),
            ch["name"],
            _fmt_mhz(ch["rx"]),
            _fmt_mhz(ch.get("tx")) or (f"{float(ch['offset']):+g}" if ch.get("offset") else ""),
            str(ch.get("mode", "")),
            str(ch.get("bw", "")),
            str(ch.get("power", "")),
            " / ".join(t for t in (tone(ch.get("rx_tone")), tone(ch.get("tx_tone"))) if t),
            "yes" if ch.get("scan", True) else "no",
        ]
        for i, ch in enumerate(lib.channels, 1)
    ]
    print_table(
        None, ["#", "name", "rx MHz", "tx / offset", "mode", "bw", "power", "tones", "scan"], channel_rows
    )


# --- profile -------------------------------------------------------------------


TX_HELP = (
    "TX of imported channels: 'profile' = the profile and the library's legal status decide "
    "(PMR446, amateur and receive-only libraries are imported receive only), 'off' = everything "
    "receive only, 'on' = TX wherever the library allows it at all – at your own responsibility."
)


def _build_from(
    profile,
    driver: Driver,
    image: bytes,
    fm: str | None,
    merge: bool | None,
    tx: str,
    tx_on: list[str] | None,
) -> Codeplug:
    caps = driver.capabilities
    warnings: list[str] = []
    cp = build(
        profile, driver.decode(image), fm_library=fm, merge=merge,
        fm_slots=caps.fm_slots, fm_name_length=caps.fm_name_length,
        tx=tx, tx_on=frozenset(tx_on or ()), rx_only_extra=caps.rx_only_extra, warnings=warnings,
    )  # fmt: skip
    for warning in warnings:
        state.err.print(f"[yellow]Warning:[/yellow] {warning}" if not state.plain else f"Warning: {warning}")
    return cp


@profile_app.command("list")
def profile_list() -> None:
    """List default configurations (built-in and user)."""
    with errors():
        found = profiles()
    rows = [
        [p.id, p.title, p.driver, p.fm_library or "-", "built-in" if p.builtin else "user"]
        for p in found.values()
    ]
    print_table("Profiles", ["profile", "title", "driver", "FM stations", "origin"], rows)
    state.err.print(f"User profiles: {paths.config_dir() / 'profiles'}")


@profile_app.command("show")
def profile_show(
    profile_id: Annotated[str, typer.Argument(help="Profile id, e.g. cz/radtel-rt900.")],
) -> None:
    """Show what a profile puts into the radio."""
    with errors():
        prof = get_profile(profile_id)
    rows = [["profile", prof.id], ["title", prof.title], ["driver", prof.driver], ["channels", prof.channels]]
    rows += [["note", n] for n in prof.notes]
    if prof.fm_library:
        rows.append(["FM stations", f"{prof.fm_library} (override with --fm)"])
    rows.append(["file", str(prof.path)])
    print_kv(rows)
    blocks = [
        [
            str(b.zone), str(b.start), b.library, str(b.count or "all"),
            b.power or ", ".join(f"{k:g} W → {v}" for k, v in (b.power_by_limit or {}).items()),
            _tx_label(b), ", ".join(f"{k}: {v}" for k, v in b.extra.items()),
        ]
        for b in prof.blocks
    ]  # fmt: skip
    print_table(None, ["zone", "from slot", "library", "count", "power", "TX", "extra"], blocks)
    print_table(None, ["setting", "value"], [[k, str(v)] for k, v in prof.settings.items()])


def _tx_label(block) -> str:
    try:
        lib = get_library(block.library)
        on = decide_tx(block, lib.tx, "profile")
    except (LibraryError, ProfileError):
        return "?"
    if on:
        return "TX"
    return "RX only" + (" (--tx on allows TX)" if lib.tx != "rx-only" else "")


def _profile_for(driver_cls: type[Driver], profile_id: str | None, country: str):
    prof = get_profile(profile_id or f"{country}/{driver_cls.id}")
    if prof.driver != driver_cls.id:
        raise ProfileError(f"profile {prof.id} is for {prof.driver}, the radio uses {driver_cls.id}")
    return prof


@profile_app.command("apply")
def profile_apply(
    radio: Annotated[str | None, typer.Argument(help="Radio alias.")] = None,
    profile_id: Annotated[
        str | None, typer.Option("--profile", "-p", help="Profile id (default: <country>/<driver>).")
    ] = None,
    country: Annotated[str, typer.Option("--country", "-c", help="Country of the default profile.")] = "cz",
    fm: Annotated[str | None, typer.Option("--fm", help="FM station library, e.g. cz/fm-ostrava.")] = None,
    merge: Annotated[
        bool | None,
        typer.Option("--merge/--replace", help="Keep channels in slots the profile does not use."),
    ] = None,
    tx: Annotated[str, typer.Option("--tx", help=TX_HELP)] = "profile",
    tx_on: Annotated[
        list[str] | None,
        typer.Option(
            "--tx-on",
            help="Switch TX on only for this library (repeatable), e.g. cz/pmr446 – at your own responsibility.",
        ),
    ] = None,
    save_yaml: Annotated[
        Path | None, typer.Option("--save", "-o", help="Also save the resulting configuration as YAML.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing --save file.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Only show what would change.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    verify: Annotated[
        bool, typer.Option("--verify/--no-verify", help="Read the radio back after writing and compare.")
    ] = True,
    driver_id: Annotated[
        str | None, typer.Option("--driver", "-d", help="Driver (without an alias).")
    ] = None,
    ble: Annotated[str | None, typer.Option("--ble", help="BLE address (without an alias).")] = None,
    port: Annotated[str | None, typer.Option("--port", help="Serial port (without an alias).")] = None,
) -> None:
    """Load a default configuration into the radio (same safe steps as write).

    Reads the radio, applies the profile on top of it, shows every change and
    asks; the radio's previous state is saved as a backup .img."""
    with errors():
        driver_cls, kind, target_addr, alias_name = resolve_radio(radio, driver_id, ble, port)
        prof = _profile_for(driver_cls, profile_id, country)
        if fm:
            get_library(fm)
        if save_yaml and save_yaml.exists() and not force:
            raise DriverError(f"{save_yaml} already exists (overwrite: --force)")

    def make_target(driver: Driver, current: bytes) -> bytes:
        return driver.encode(_build_from(prof, driver, current, fm, merge, tx, tx_on), current)

    _write_flow(
        driver_cls, kind, target_addr, alias_name, alias_name or driver_cls.id, make_target,
        dry_run=dry_run, yes=yes, verify=verify, save_yaml=save_yaml,
    )  # fmt: skip


@profile_app.command("build")
def profile_build(
    image_file: Annotated[Path, typer.Argument(help="Backup .img of the radio (the base).", exists=True)],
    output: Annotated[Path, typer.Option("--output", "-o", help="Target YAML.")],
    profile_id: Annotated[str | None, typer.Option("--profile", "-p", help="Profile id.")] = None,
    country: Annotated[str, typer.Option("--country", "-c", help="Country of the default profile.")] = "cz",
    driver_id: Annotated[
        str | None, typer.Option("--driver", "-d", help="Driver (default: by file size).")
    ] = None,
    fm: Annotated[str | None, typer.Option("--fm", help="FM station library, e.g. cz/fm-ostrava.")] = None,
    merge: Annotated[bool | None, typer.Option("--merge/--replace", help="Keep other channels.")] = None,
    tx: Annotated[str, typer.Option("--tx", help=TX_HELP)] = "profile",
    tx_on: Annotated[
        list[str] | None,
        typer.Option(
            "--tx-on",
            help="Switch TX on only for this library (repeatable), e.g. cz/pmr446 – at your own responsibility.",
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing YAML.")] = False,
) -> None:
    """Build a configuration YAML from a profile without a radio (to review or edit,
    then load it with: wokitoki write <alias> <yaml>)."""
    with errors():
        image = _strip_chirp(image_file.read_bytes())
        driver_cls = get_driver(driver_id) if driver_id else _driver_by_size(image)
        driver = driver_cls()
        prof = _profile_for(driver_cls, profile_id, country)
        if output.exists() and not force:
            raise DriverError(f"{output} already exists (overwrite: --force)")
        cp = _build_from(prof, driver, image, fm, merge, tx, tx_on)
        target = driver.encode(cp, image)  # validates everything like a real write would
        driver.check_write(image, target)
        # the profile's notes ("Built from profile …") go into the saved YAML
        _save_codeplug(
            driver, target, output, image_file, alias=None, model=None, read_at=None, notes=cp.notes
        )
    state.console.print(
        f"Saved {output} ({driver.decode(target).channel_count} channels) – base {image_file.name}"
    )


# --- alias -------------------------------------------------------------------


@alias_app.command("add")
def alias_add(
    name: Annotated[str, typer.Argument(help="Alias name (a–z, 0–9, - and _).")],
    driver_id: Annotated[str, typer.Option("--driver", "-d", help="Driver, see: wokitoki radios.")],
    ble: Annotated[str | None, typer.Option("--ble", help="BLE address (macOS UUID / MAC).")] = None,
    port: Annotated[str | None, typer.Option("--port", help="Serial port (COM3, /dev/ttyUSB0…).")] = None,
    device_name: Annotated[
        str | None, typer.Option("--name", help="How the device advertises itself.")
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="Note.")] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing alias.")] = False,
) -> None:
    """Add an alias by hand (without scanning)."""
    with errors():
        if bool(ble) == bool(port):
            raise AliasError("give exactly one of --ble / --port")
        driver = get_driver(driver_id)
        kind = "ble" if ble else "serial"
        if kind not in driver.transports():
            raise AliasError(f"{driver_id} does not support connection '{kind}'")
        store = AliasStore()
        store.add(
            Alias(
                name=name,
                driver=driver_id,
                transport=kind,
                address=ble,
                port=port,
                device_name=device_name,
                note=note,
            ),
            replace=force,
        )
        store.save()
    state.console.print(f"Alias [bold]{name}[/bold] saved.")


@alias_app.command("list")
def alias_list() -> None:
    """List aliases."""
    with errors():
        aliases = AliasStore().all()
    if not aliases:
        state.console.print("No aliases. Add one: wokitoki scan --save / wokitoki alias add …")
        return
    rows = [[a.name, a.driver, f"{a.transport} {a.target}", a.note or ""] for a in aliases]
    print_table("Aliases", ["alias", "driver", "connection", "note"], rows)


@alias_app.command("show")
def alias_show(name: Annotated[str, typer.Argument(help="Alias.")]) -> None:
    """Alias details."""
    with errors():
        alias = AliasStore().get(name)
    rows = [
        ["alias", alias.name],
        ["driver", alias.driver],
        ["connection", alias.transport],
        ["address" if alias.transport == "ble" else "port", alias.target],
    ]
    if alias.device_name:
        rows.append(["name", alias.device_name])
    if alias.note:
        rows.append(["note", alias.note])
    if alias.added:
        rows.append(["added", alias.added.isoformat()])
    rows.append(["data", str(paths.radio_dir(alias.name))])
    print_kv(rows)


@alias_app.command("rename")
def alias_rename(
    old: Annotated[str, typer.Argument(help="Current name.")],
    new: Annotated[str, typer.Argument(help="New name.")],
) -> None:
    """Rename an alias."""
    with errors():
        store = AliasStore()
        store.rename(old, new)
        store.save()
    state.console.print(f"Alias {old} → [bold]{new}[/bold].")
    old_dir = paths.radio_dir(old)
    if old_dir.exists():
        state.console.print(
            f"Note: the data stayed in {old_dir} – move it to {paths.radio_dir(new)} if you want."
        )


@alias_app.command("rm")
def alias_rm(
    name: Annotated[str, typer.Argument(help="Alias.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="No confirmation.")] = False,
) -> None:
    """Delete an alias (radio data in ~/wokitoki/<alias>/ is kept)."""
    with errors():
        store = AliasStore()
        store.get(name)
        if not yes and not typer.confirm(f"Delete alias {name}?"):
            raise typer.Exit(1)
        store.remove(name)
        store.save()
    state.console.print(f"Alias {name} deleted.")


@alias_app.command("edit")
def alias_edit() -> None:
    """Open aliases.yaml in an editor ($VISUAL / $EDITOR)."""
    with errors():
        store = AliasStore()
        if not store.path.exists():
            store.save()
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        if os.name == "nt":  # posix=False keeps the quotes around "C:\Program Files\…"
            cmd = [part.strip('"') for part in shlex.split(editor, posix=False)]
        else:
            cmd = shlex.split(editor)
        cmd.append(str(store.path))
        subprocess.run(cmd, check=False)
    else:
        typer.launch(str(store.path))
        state.console.print(f"Opening {store.path} in the default application.")
        return
    with errors():
        count = len(AliasStore().all())
    state.console.print(f"aliases.yaml is valid ({count} aliases).")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
