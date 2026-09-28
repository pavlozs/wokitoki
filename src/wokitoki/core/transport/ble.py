"""Bluetooth LE transport (bleak) for HM-10 style "transparent UART" radios.

Radio specifics (UUIDs, unlock sequence, name hints) come from the driver as
a :class:`BleProfile`; this module knows nothing about any particular radio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .base import BufferedTransport, TransportError, hexdump

log = logging.getLogger("wokitoki.transport")


def uuid16(short: str) -> str:
    """Expand a 16-bit UUID (``"FFE1"``) to the full Bluetooth base UUID."""
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class BleUnlock:
    """A one-time write that has to happen after connecting (RT-950 Pro: FF31)."""

    uuid: str
    payload: bytes
    settle: float = 0.4
    # Notifications starting with this prefix are the radio's reply to the
    # unlock; they are dropped until the first data write.
    reply_prefix: bytes | None = None


@dataclass(frozen=True)
class BleProfile:
    service_uuid: str
    data_uuid: str  # notify (radio → us) and write (us → radio)
    write_uuid: str | None = None  # when writes go to another characteristic
    unlock: BleUnlock | None = None
    chunk_size: int = 20
    name_hints: tuple[str, ...] = ()


@dataclass
class BleAdvert:
    """One device seen during a BLE scan (independent of bleak types)."""

    address: str
    name: str | None
    rssi: int | None
    service_uuids: list[str] = field(default_factory=list)


async def scan_ble(timeout: float = 8.0) -> list[BleAdvert]:
    from bleak import BleakScanner
    from bleak.exc import BleakError

    try:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except BleakError as e:
        raise TransportError(f"Bluetooth scan failed: {e}") from e
    adverts = [
        BleAdvert(
            # macOS returns pyobjc_unicode (a str subclass); keep plain str.
            address=str.__str__(device.address),
            name=str.__str__(n) if (n := adv.local_name or device.name) else None,
            rssi=adv.rssi,
            service_uuids=[u.lower() for u in adv.service_uuids],
        )
        for device, adv in found.values()
    ]
    adverts.sort(key=lambda a: -(a.rssi if a.rssi is not None else -999))
    return adverts


class BleTransport(BufferedTransport):
    kind = "ble"

    def __init__(self, address: str, profile: BleProfile, *, connect_timeout: float = 20.0):
        super().__init__()
        self.address = address
        self.profile = profile
        self.connect_timeout = connect_timeout
        self._client = None
        self._write_char = None
        self._drop_unlock_reply = False

    @property
    def description(self) -> str:
        return f"ble {self.address}"

    async def open(self) -> None:
        from bleak import BleakClient
        from bleak.exc import BleakError

        log.info("connecting to %s", self.address)
        self._client = BleakClient(
            self.address, disconnected_callback=self._on_disconnect, timeout=self.connect_timeout
        )
        try:
            await self._client.connect()
        except BaseException as e:
            await self.close()  # a half-open link would keep the radio busy
            if isinstance(e, BleakError | TimeoutError | OSError):
                raise TransportError(f"cannot connect to {self.address}: {e or type(e).__name__}") from e
            raise
        self._connected = True
        try:
            await self._setup()
        except BaseException as e:
            await self.close()
            if isinstance(e, BleakError | OSError | EOFError):
                raise TransportError(f"BLE setup of {self.address} failed: {e or type(e).__name__}") from e
            raise

    async def _setup(self) -> None:
        services = self._client.services
        data_char = services.get_characteristic(self.profile.data_uuid)
        if data_char is None:
            raise TransportError(
                f"characteristic {self.profile.data_uuid} not found – is this the right radio/driver?"
            )
        self._write_char = (
            services.get_characteristic(self.profile.write_uuid) if self.profile.write_uuid else data_char
        )
        if self._write_char is None:
            raise TransportError(f"write characteristic {self.profile.write_uuid} not found")

        # Order verified on the RT-950 Pro: notify first, then unlock.
        await self._client.start_notify(data_char, self._on_notify)
        unlock = self.profile.unlock
        if unlock is not None:
            unlock_char = services.get_characteristic(unlock.uuid)
            if unlock_char is None:
                log.warning("unlock characteristic %s not found, continuing without unlock", unlock.uuid)
            else:
                self._drop_unlock_reply = unlock.reply_prefix is not None
                log.debug("TX [unlock %s] %s", unlock.uuid, hexdump(unlock.payload))
                await self._client.write_gatt_char(
                    unlock_char, unlock.payload, response="write" in unlock_char.properties
                )
                await asyncio.sleep(unlock.settle)
        self.reset_input()

    async def close(self) -> None:
        client, self._client = self._client, None
        self._connected = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:  # noqa: BLE001 – already gone, nothing else to do
                log.debug("disconnect: %s", e)

    async def write(self, data: bytes, *, confirm: bool = False) -> None:
        if not self._connected or self._client is None:
            raise TransportError("the radio is not connected")
        from bleak.exc import BleakError

        self._drop_unlock_reply = False
        self._log_tx(data)
        props = self._write_char.properties
        if confirm:
            response = "write" in props
        else:
            response = "write-without-response" not in props
        step = self.profile.chunk_size
        try:
            for offset in range(0, len(data), step):
                if not response:
                    await self._wait_ready_to_send()
                await self._client.write_gatt_char(self._write_char, data[offset : offset + step], response)
        except (BleakError, OSError, EOFError) as e:
            raise TransportError(f"BLE write failed: {e or type(e).__name__}") from e

    async def _wait_ready_to_send(self) -> None:
        # bleak does no flow control for write-without-response on macOS and
        # CoreBluetooth silently drops packets when its queue is full
        # (seen in the RT950Pro app). Poll the peripheral where possible.
        peripheral = getattr(getattr(self._client, "_backend", None), "_peripheral", None)
        can_send = getattr(peripheral, "canSendWriteWithoutResponse", None)
        if can_send is None:
            return
        deadline = time.monotonic() + 3.0
        while not can_send():
            if not self._connected:
                raise TransportError("connection lost while writing")
            if time.monotonic() > deadline:
                raise TransportError("BLE send queue full")
            await asyncio.sleep(0.002)

    def _on_notify(self, _char, data: bytearray) -> None:
        prefix = self.profile.unlock.reply_prefix if self.profile.unlock else None
        if self._drop_unlock_reply and prefix and bytes(data).startswith(prefix):
            log.debug("RX [unlock reply, dropped] %s", hexdump(bytes(data)))
            return
        self._feed(bytes(data))

    def _on_disconnect(self, client) -> None:
        if client is not self._client:
            return  # a late callback from an earlier connection of this object
        if self._connected:
            log.info("the radio closed the BLE connection")
        self._lost()
