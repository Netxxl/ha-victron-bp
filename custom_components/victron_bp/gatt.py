"""Persistent read-only VE.Direct Smart GATT session.

The device speaks a chunked transport with flow control. Enabling notifications
and asking for registers is not enough: the device streams nothing until it has
been given "ready to receive" credits on the ...0002 characteristic (opcode
0xF9), after a chunk-size negotiation (0xFA). This is confirmed in birdie's
captured frames and on the device.

So per vendor instance we: enable notifications on ...0002/0003/0004, send the
chunk-size + ready-to-receive credits on ...0002, a warm-up and a ping on ...0003,
and the register subscribes on ...0004. Credits are topped up every poll. Nothing
here writes a value register, so it cannot change anything on the device.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

try:
    from bleak_retry_connector import close_stale_connections_by_address
except ImportError:  # older bleak-retry-connector
    close_stale_connections_by_address = None

from .catalog import VregDef
from .vesmart import (
    CHUNK_SIZE_FRAME,
    PING_FRAME,
    READY_FRAME,
    READY_INIT_FRAME,
    WARMUP_CONTROL,
    scan_for_registers,
    subscribe_frame,
    write_frame,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0
COLLECT_SECONDS = 3.0
MAX_BUFFER = 8192

# The device drops the link if the VE.Direct session goes quiet: after a single
# init burst it streams its backlog for ~10 s and then, with no fresh credits or
# ping, disconnects (seen as a regular ~11-20 s "link dropped" at -47 dBm on both
# adapters). The coordinator poll is far too slow to keep it alive (60 s), so a
# background task tops up credits and pings every few seconds while connected.
KEEPALIVE_SECONDS = 5.0

_VENDOR_PREFIXES = ("306b", "9758")
_NOTIFY_PROPS = frozenset({"notify", "indicate"})


@dataclass
class _Instance:
    """The 0002/0003/0004 characteristic triple of one vendor service."""

    flow: BleakGATTCharacteristic | None = None     # ...0002: chunk size + credits
    control: BleakGATTCharacteristic | None = None  # ...0003: warm-up, ping
    stream: BleakGATTCharacteristic | None = None   # ...0004: subscribes + bulk data
    notifying: list[BleakGATTCharacteristic] = field(default_factory=list)


class VeSmartConnection:
    """One long-lived read-only connection to the device."""

    def __init__(
        self,
        device_getter: Callable[[], BLEDevice | None],
        name: str,
        registers: dict[int, VregDef],
    ) -> None:
        self._device_getter = device_getter
        self._name = name
        self._registers = dict(registers)
        self._client: BleakClient | None = None
        self._buffer = bytearray()
        self._lock = asyncio.Lock()
        self._instances: list[_Instance] = []
        self._keepalive_task: asyncio.Task | None = None
        self.values: dict[int, float | int | None] = {}
        # BlueZ refused a notify with "Notify acquired" on the last connect: a stale
        # flag from the previous link that only RemoveDevice clears (pairing.async_reset)
        self.notify_locked = False

    def _on_notify(self, char: BleakGATTCharacteristic, data: bytearray) -> None:
        # No per-notify logging: the device streams ~1/s (mostly its own uptime
        # tick), which floods the log and the SSD. The once-per-poll summary in
        # poll() is enough; raw frames can be re-enabled here if needed.
        self._buffer += bytes(data)
        if len(self._buffer) > MAX_BUFFER:
            del self._buffer[:-MAX_BUFFER]
        found = scan_for_registers(bytes(self._buffer), self._registers)
        if found:
            self.values.update(found)

    def _on_disconnect(self, _client: BleakClient) -> None:
        _LOGGER.debug("%s: link dropped", self._name)
        self._client = None
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None

    async def _write(self, char: BleakGATTCharacteristic, frame: bytes) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.write_gatt_char(char, frame, response=False)
        except Exception as err:
            _LOGGER.debug("%s: write %s -> %s failed: %s", self._name, frame.hex(), char.uuid, err)

    async def _connect(self) -> None:
        device = self._device_getter()
        if device is None:
            raise RuntimeError(f"{self._name} not in range")

        # Only clear a stale bleak/HA-side connection object. Do NOT force a
        # BlueZ-level disconnect (async_disconnect) or run async_diagnose on every
        # connect: that was scaffolding for the BatteryProtect "cdfd0 notify
        # acquired" hunt, and the churn -- a forced teardown, a 1.5 s wait and a
        # full ObjectManager enumeration on a second D-Bus -- tears the fresh,
        # fragile link down again before we can enable notifications (seen as
        # "resolved -> ~2 s -> link dropped" with buffer 0). The cdfd0 lock is
        # cured by RemoveDevice (pairing.async_reset), which the coordinator runs
        # when notify_locked is set -- not here.
        if close_stale_connections_by_address is not None:
            with contextlib.suppress(Exception):
                await close_stale_connections_by_address(self._name)

        client = await establish_connection(
            BleakClient,
            device,
            self._name,
            disconnected_callback=self._on_disconnect,
            timeout=CONNECT_TIMEOUT,
        )
        self._client = client

        # Group the vendor characteristics into 0002/0003/0004 triples per service.
        by_service: dict[str, _Instance] = {}
        for service in client.services:
            _LOGGER.debug("%s: service %s", self._name, service.uuid)
            for char in service.characteristics:
                _LOGGER.debug("%s:   char %s %s", self._name, char.uuid, char.properties)
                uuid = char.uuid.lower()
                if uuid[:4] not in _VENDOR_PREFIXES:
                    continue
                inst = by_service.setdefault(service.uuid, _Instance())
                slot = uuid[4:8]
                if slot == "0002":
                    inst.flow = char
                elif slot == "0003":
                    inst.control = char
                elif slot == "0004":
                    inst.stream = char

        # Enable notifications; keep only instances whose stream char we can hear.
        self._instances = []
        self.notify_locked = False
        for inst in by_service.values():
            for char in (inst.flow, inst.control, inst.stream):
                if char is None or not _NOTIFY_PROPS.intersection(char.properties):
                    continue
                if self._client is None:
                    break
                try:
                    await self._client.start_notify(char, self._on_notify)
                    inst.notifying.append(char)
                except Exception as err:
                    _LOGGER.debug("%s: notify %s failed: %s", self._name, char.uuid, err)
                    if "Notify acquired" in str(err):
                        self.notify_locked = True
            if inst.stream is not None and inst.stream in inst.notifying:
                self._instances.append(inst)
        _LOGGER.debug(
            "%s: active instances %s",
            self._name,
            [i.stream.uuid for i in self._instances],
        )

        await self._init()
        self._start_keepalive()

    def _start_keepalive(self) -> None:
        """Begin pinging in the background so the device keeps the link up."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
        self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Top up credits and ping every few seconds until the link drops.

        Runs outside the 60 s coordinator poll: without this the device goes
        quiet after its init burst and disconnects within ~10 s. Each kick takes
        the connection lock so it never overlaps a reconnect.
        """
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_SECONDS)
                async with self._lock:
                    if self._client is None or not self._client.is_connected:
                        return
                    await self._kick()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # keep the task alive-safe; a bad kick isn't fatal
            _LOGGER.debug("%s: keepalive stopped: %s", self._name, err)

    async def _init(self) -> None:
        """Full start sequence: chunk size, credits, warm-up, subscribes."""
        for inst in self._instances:
            if inst.flow is not None:
                await self._write(inst.flow, CHUNK_SIZE_FRAME)
                await self._write(inst.flow, READY_INIT_FRAME)
            if inst.control is not None:
                for frame in (*WARMUP_CONTROL, PING_FRAME):
                    await self._write(inst.control, frame)
            if inst.stream is not None:
                for vreg in self._registers:
                    await self._write(inst.stream, subscribe_frame(vreg))

    async def _kick(self) -> None:
        """Top up receive credits and ping, so the stream keeps coming."""
        for inst in self._instances:
            if inst.flow is not None:
                await self._write(inst.flow, READY_FRAME)
            if inst.control is not None:
                await self._write(inst.control, PING_FRAME)

    async def poll(self) -> dict[int, float | int | None]:
        """Ensure the connection is up, nudge the stream, return latest values.

        A connection problem is logged but not raised, so the config entry (and
        with it the "pair" button) stays available even before the device is
        bonded or in range.
        """
        # Never open or tear down a connection while a bond is in progress: a
        # forced disconnect / connect here lands in the middle of Pair() and
        # BlueZ aborts the bond with AuthenticationCanceled. Let pairing finish.
        from .pairing import pairing_in_progress

        if pairing_in_progress(self._name):
            _LOGGER.debug("%s: poll skipped, pairing in progress", self._name)
            return dict(self.values)

        async with self._lock:
            try:
                if self._client is None or not self._client.is_connected:
                    await self._connect()
                else:
                    await self._kick()
            except Exception as err:
                _LOGGER.debug("%s: connect/kick failed: %s", self._name, err)
                return dict(self.values)

            await asyncio.sleep(COLLECT_SECONDS)
            _LOGGER.debug(
                "%s: %d values (buffer %d bytes): %s",
                self._name,
                len(self.values),
                len(self._buffer),
                {hex(k): v for k, v in self.values.items()},
            )
            return dict(self.values)

    async def write_register(self, vreg: int, value: bytes) -> None:
        """Write one register (e.g. VE_REG_MODE) on the primary vendor instance.

        Uses the same connection and lock as polling, so it never races the
        keepalive or a reconnect. Writes only to the first instance's control
        characteristic (the 306b...cdfd0 channel that carries the data), not the
        second channel, so a multi-channel device is not toggled wholesale.
        """
        async with self._lock:
            if self._client is None or not self._client.is_connected:
                await self._connect()
            if not self._instances or self._instances[0].control is None:
                raise RuntimeError(f"{self._name}: no control characteristic to write to")
            frame = write_frame(vreg, value)
            await self._client.write_gatt_char(
                self._instances[0].control, frame, response=False
            )
            _LOGGER.debug("%s: wrote vreg 0x%04x = %s", self._name, vreg, value.hex())

    async def stop(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        async with self._lock:
            client = self._client
            self._client = None
            if client is None:
                return
            for inst in self._instances:
                for char in inst.notifying:
                    with contextlib.suppress(Exception):
                        await client.stop_notify(char)
            with contextlib.suppress(Exception):
                await client.disconnect()
