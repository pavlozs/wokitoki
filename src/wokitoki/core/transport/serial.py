"""Serial port / USB cable transport (pyserial). Blocking I/O runs in a thread."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .base import Transport, TransportError, TransportTimeout

log = logging.getLogger("wokitoki.transport")


@dataclass(frozen=True)
class SerialProfile:
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    # USB IDs of radios with their *own* USB interface. Never list generic
    # USB-serial chips here – they say nothing about the radio behind them.
    usb_ids: tuple[tuple[int, int], ...] = ()


# Generic USB-serial chips used in radio programming cables. A match means
# "this is a programming cable", not which radio is connected – only a
# handshake tells that (e.g. `wokitoki ping --driver … --port …`).
USB_SERIAL_CHIPS: dict[tuple[int, int], str] = {
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x7522): "CH340K",
    (0x1A86, 0x55D4): "CH9102",
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FTDI FT232R",
    (0x0403, 0x6015): "FTDI FT231X",
    (0x067B, 0x2303): "PL2303",
    (0x067B, 0x23A3): "PL2303GC",
}


@dataclass
class SerialPortInfo:
    port: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None

    @property
    def cable_chip(self) -> str | None:
        """Name of a known USB-serial chip (programming cable), if any."""
        return USB_SERIAL_CHIPS.get((self.vid, self.pid)) if self.vid is not None else None


def list_serial_ports() -> list[SerialPortInfo]:
    from serial.tools import list_ports

    return [
        SerialPortInfo(p.device, _clean(p.description), p.vid, p.pid, p.serial_number)
        for p in sorted(list_ports.comports(), key=lambda p: p.device)
    ]


def _clean(description: str | None) -> str:
    return "" if not description or description == "n/a" else description


class SerialTransport(Transport):
    kind = "serial"

    def __init__(self, port: str, profile: SerialProfile):
        self.port = port
        self.profile = profile
        self._serial = None

    @property
    def description(self) -> str:
        return f"serial {self.port}"

    async def open(self) -> None:
        import serial

        try:
            self._serial = await asyncio.to_thread(
                serial.Serial,
                self.port,
                baudrate=self.profile.baudrate,
                bytesize=self.profile.bytesize,
                parity=self.profile.parity,
                stopbits=self.profile.stopbits,
                timeout=1.0,
            )
        except serial.SerialException as e:
            raise TransportError(f"cannot open {self.port}: {e}") from e

    async def close(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            await asyncio.to_thread(port.close)

    def _port(self):
        if self._serial is None:
            raise TransportError("serial port not open")
        return self._serial

    async def write(self, data: bytes, *, confirm: bool = False) -> None:
        import serial

        port = self._port()
        self._log_tx(data)
        try:
            await asyncio.to_thread(self._write_all, port, data)
        except (serial.SerialException, OSError) as e:
            raise TransportError(f"write to {self.port} failed: {e}") from e
        except Exception as e:  # termios.error from flush() when the cable is gone
            raise TransportError(f"write to {self.port} failed: {e}") from e

    @staticmethod
    def _write_all(port, data: bytes) -> None:
        port.write(data)
        port.flush()

    async def read_exact(self, n: int, timeout: float) -> bytes:
        import serial

        port = self._port()
        try:
            data = await asyncio.to_thread(self._read, port, n, timeout)
        except (serial.SerialException, OSError) as e:
            raise TransportError(f"read from {self.port} failed: {e}") from e
        if data:
            self._log_rx(data)
        if len(data) < n:
            raise TransportTimeout(len(data), n, timeout)
        return data

    @staticmethod
    def _read(port, n: int, timeout: float) -> bytes:
        if port.timeout != timeout:  # the setter reconfigures the port (slow on Windows)
            port.timeout = timeout
        return port.read(n)

    def reset_input(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.reset_input_buffer()
        except Exception as e:  # SerialException, OSError or termios.error (cable unplugged)
            raise TransportError(f"{self.port}: {e}") from e
