"""Transport interface: a plain byte pipe shared by BLE and serial.

Drivers only ever see this interface, never bleak or pyserial directly.
Every byte sent and received is logged at DEBUG level on the
``wokitoki.transport`` logger (enabled with ``wokitoki -v``).
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Self

log = logging.getLogger("wokitoki.transport")


class TransportError(Exception):
    """Connection-level failure (cannot connect, link lost, write failed)."""


class TransportTimeout(TransportError):
    def __init__(self, received: int, expected: int, timeout: float):
        super().__init__(f"no answer from the radio within {timeout:g} s ({received}/{expected} B received)")
        self.received = received
        self.expected = expected


def hexdump(data: bytes) -> str:
    return data.hex(" ").upper()


class Transport(ABC):
    """Async byte pipe. Use as ``async with transport: ...``."""

    kind: str = "?"

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable target, e.g. ``ble A1B2C3D4-…`` or ``serial COM3``."""

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def write(self, data: bytes, *, confirm: bool = False) -> None:
        """Send bytes. ``confirm`` asks for a link-level acknowledgement
        (BLE write-with-response) where the transport supports it."""

    @abstractmethod
    async def read_exact(self, n: int, timeout: float) -> bytes:
        """Return exactly ``n`` bytes or raise :class:`TransportTimeout`."""

    @abstractmethod
    def reset_input(self) -> None:
        """Drop everything received but not yet read."""

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    def _log_tx(self, data: bytes) -> None:
        if log.isEnabledFor(logging.DEBUG):
            log.debug("TX %s", hexdump(data))

    def _log_rx(self, data: bytes) -> None:
        if log.isEnabledFor(logging.DEBUG):
            log.debug("RX %s", hexdump(data))


class BufferedTransport(Transport):
    """Transport fed asynchronously (BLE notifications, test fakes):
    incoming bytes are appended to a buffer that ``read_exact`` drains."""

    def __init__(self) -> None:
        self._rx = bytearray()
        self._rx_event = asyncio.Event()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _feed(self, data: bytes) -> None:
        self._log_rx(data)
        self._rx += data
        self._rx_event.set()

    def _lost(self) -> None:
        self._connected = False
        self._rx_event.set()

    def reset_input(self) -> None:
        self._rx.clear()

    async def read_exact(self, n: int, timeout: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # A fresh Event per read: an Event binds to the loop it first waits in and
        # the CLI runs every phase (read, write, verify) in its own asyncio.run().
        self._rx_event = asyncio.Event()
        while len(self._rx) < n:
            if not self._connected:
                raise TransportError(f"connection lost while reading ({len(self._rx)}/{n} B received)")
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TransportTimeout(len(self._rx), n, timeout)
            self._rx_event.clear()
            try:
                await asyncio.wait_for(self._rx_event.wait(), remaining)
            except TimeoutError:
                pass
        data = bytes(self._rx[:n])
        del self._rx[:n]
        return data
