"""One-time BLE bonding with the 6-digit PIN, done from inside Home Assistant.

VE.Direct Smart gates the GATT connection with a BLE bond: the 6-digit PIN is
the pairing passkey, and the link is encrypted. The PIN is only needed once, for
this bond; afterwards BlueZ reuses the stored key and the device is marked
trusted, so later connections need no PIN.

Home Assistant's Bluetooth stack does not enter a passkey on its own, so we
briefly register our own BlueZ pairing agent on the system bus, run Pair() on
that same D-Bus connection (BlueZ then asks *our* agent for the passkey), set
the device trusted and unregister again. Nothing here reads or writes values.

Note: no ``from __future__ import annotations`` here on purpose. dbus-fast reads
the method type annotations ("o", "u", ...) at runtime; PEP 563 would turn them
into the wrong strings and break the agent interface.
"""

import asyncio
import contextlib
import logging

_LOGGER = logging.getLogger(__name__)

BLUEZ = "org.bluez"
ROOT = "/org/bluez"
AGENT_PATH = "/io/netxxl/victron_bp/agent"
AGENT_IFACE = "org.bluez.Agent1"
DEVICE_IFACE = "org.bluez.Device1"
ADAPTER_IFACE = "org.bluez.Adapter1"
CHAR_IFACE = "org.bluez.GattCharacteristic1"
AGENTMGR_IFACE = "org.bluez.AgentManager1"
OM_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

PAIR_TIMEOUT = 30.0

# Vendor GATT services we care about (VE.Direct Smart 306b..., device info 9758...).
_VENDOR_PREFIXES = ("306b", "9758")


class PairingError(Exception):
    """Pairing could not be completed."""


# Addresses with a bond in progress. The read coordinator must not open or tear
# down a connection while we bond: a poll's forced disconnect / connect lands in
# the middle of Pair() and BlueZ aborts it with AuthenticationCanceled (and no
# passkey is ever requested). The coordinator checks pairing_in_progress() and
# skips its cycle; the pair/reset buttons hold pairing_guard() around the bond.
# Reentrant (counter) so overlapping guards nest safely.
_pairing_counts: dict[str, int] = {}


def pairing_in_progress(address: str) -> bool:
    return _pairing_counts.get(address.upper(), 0) > 0


@contextlib.contextmanager
def pairing_guard(address: str):
    key = address.upper()
    _pairing_counts[key] = _pairing_counts.get(key, 0) + 1
    try:
        yield
    finally:
        left = _pairing_counts.get(key, 0) - 1
        if left <= 0:
            _pairing_counts.pop(key, None)
        else:
            _pairing_counts[key] = left


def _build_agent(pin: str):
    """Build the Agent1 D-Bus object. Imported lazily so the module loads
    everywhere (dbus-fast only exists on the Home Assistant host)."""
    from dbus_fast.service import ServiceInterface, method

    passkey = int(pin)

    class _PinAgent(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(AGENT_IFACE)

        @method()
        def Release(self) -> None:  # noqa: N802
            return

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: N802,F821,UP037
            return pin

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: N802,F821,UP037
            _LOGGER.debug("pairing agent: passkey requested for %s", device)
            return passkey

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:  # noqa: N802,F821,UP037
            return

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s") -> None:  # noqa: N802,F821,UP037
            return

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u") -> None:  # noqa: N802,F821,UP037
            return  # accept

        @method()
        def RequestAuthorization(self, device: "o") -> None:  # noqa: N802,F821,UP037
            return  # accept

        @method()
        def AuthorizeService(self, device: "o", uuid: "s") -> None:  # noqa: N802,F821,UP037
            return  # accept

        @method()
        def Cancel(self) -> None:  # noqa: N802
            return

    return _PinAgent()


async def _managed_objects(bus) -> dict:
    intro = await bus.introspect(BLUEZ, "/")
    root = bus.get_proxy_object(BLUEZ, "/", intro)
    om = root.get_interface(OM_IFACE)
    return await om.call_get_managed_objects()


async def _find_device_path(bus, address: str, attempts: int = 8, delay: float = 1.0) -> str | None:
    target = address.upper()
    for _ in range(attempts):
        for path, ifaces in (await _managed_objects(bus)).items():
            dev = ifaces.get(DEVICE_IFACE)
            if not dev:
                continue
            addr = dev.get("Address")
            if addr is not None and addr.value.upper() == target:
                return path
        await asyncio.sleep(delay)
    return None


async def async_diagnose(address: str) -> None:
    """Dump the real BlueZ state for this MAC *before* we subscribe anything.

    This is how we tell apart the two competing explanations for the
    "Notify acquired" on instance 0:

    * If a characteristic already reports ``Notifying=True`` while we have not
      touched it, something else holds an active notify session -- and the
      device's adapter path shows *where* (a second local ``hci`` or an ESPHome
      proxy object for the same MAC).
    * If nothing is notifying yet StartNotify still fails, the problem is on our
      side (AcquireNotify fd vs StartNotify), not another client.

    Read-only: only D-Bus properties are read, nothing is written.
    """
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    target = address.upper()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        objects = await _managed_objects(bus)

        # Every Device1 object for this MAC (there can be more than one: a local
        # adapter copy and a proxy copy live under different adapter paths).
        dev_paths: list[str] = []
        for path, ifaces in objects.items():
            dev = ifaces.get(DEVICE_IFACE)
            if not dev:
                continue
            addr = dev.get("Address")
            if addr is not None and addr.value.upper() == target:
                dev_paths.append(path)
                adapter = dev.get("Adapter")
                connected = dev.get("Connected")
                resolved = dev.get("ServicesResolved")
                _LOGGER.debug(
                    "%s: diag device %s adapter=%s connected=%s resolved=%s",
                    address,
                    path,
                    adapter.value if adapter else "?",
                    connected.value if connected else "?",
                    resolved.value if resolved else "?",
                )

        if not dev_paths:
            _LOGGER.debug("%s: diag found no device object in BlueZ", address)
            return

        # Characteristics live under their device path. Report the vendor ones
        # with their current Notifying state and flags.
        for path, ifaces in sorted(objects.items()):
            char = ifaces.get(CHAR_IFACE)
            if not char:
                continue
            if not any(path.startswith(dev + "/") for dev in dev_paths):
                continue
            uuid = char.get("UUID")
            uuid_val = uuid.value.lower() if uuid else ""
            if uuid_val[:4] not in _VENDOR_PREFIXES:
                continue
            notifying = char.get("Notifying")
            flags = char.get("Flags")
            _LOGGER.debug(
                "%s: diag char %s notifying=%s flags=%s",
                address,
                uuid_val,
                notifying.value if notifying else "?",
                flags.value if flags else "?",
            )
    finally:
        with contextlib.suppress(Exception):
            bus.disconnect()


async def async_disconnect(address: str) -> None:
    """Force a BlueZ-level ACL disconnect of the device on *every* adapter.

    The host can expose the same MAC under more than one adapter (``hci0`` and
    ``hci1`` here). The live connection -- and with it the notify session that is
    "acquired" on instance 0 -- may sit on a different adapter than the first
    object BlueZ hands back. Disconnecting only the first match (as this used to)
    leaves the real link up, so the next connect adopts it with instance 0 still
    locked. We therefore disconnect *all* matching objects, connected ones first,
    which tears down the ACL and clears all CCC/acquire state on that adapter.
    """
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    target = address.upper()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        # Collect every Device1 object for this MAC and whether it is connected.
        matches: list[tuple[str, bool | None]] = []
        for _ in range(3):
            matches = []
            for path, ifaces in (await _managed_objects(bus)).items():
                dev = ifaces.get(DEVICE_IFACE)
                if not dev:
                    continue
                addr = dev.get("Address")
                if addr is None or addr.value.upper() != target:
                    continue
                connected = dev.get("Connected")
                matches.append((path, connected.value if connected else None))
            if matches:
                break
            await asyncio.sleep(0.5)

        if not matches:
            _LOGGER.debug("%s: forced disconnect: device not found in BlueZ", address)
            return

        # Connected objects first, so we hit the adapter that holds the link.
        matches.sort(key=lambda m: m[1] is not True)
        for path, connected in matches:
            try:
                intro = await bus.introspect(BLUEZ, path)
                dev = bus.get_proxy_object(BLUEZ, path, intro).get_interface(DEVICE_IFACE)
                await dev.call_disconnect()
                _LOGGER.debug("%s: forced BlueZ disconnect on %s (was connected=%s)", address, path, connected)
            except Exception as err:
                _LOGGER.debug("%s: forced disconnect on %s failed: %s", address, path, err)
    finally:
        with contextlib.suppress(Exception):
            bus.disconnect()


async def async_remove_device(address: str) -> int:
    """Make BlueZ forget the device on every adapter (Adapter1.RemoveDevice).

    A plain Disconnect() leaves the device *known* and *services-resolved*, so
    BlueZ keeps the cached GATT table -- including a stale ``notify_acquired``
    flag on an instance-0 characteristic that then refuses every StartNotify
    with "Notify acquired". RemoveDevice drops the whole cached entry (and the
    bond), so the next connect rebuilds the GATT table from scratch and the flag
    is gone. The caller must bond again afterwards.

    Returns how many device objects were removed.
    """
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    target = address.upper()
    removed = 0
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        paths: list[str] = []
        for path, ifaces in (await _managed_objects(bus)).items():
            dev = ifaces.get(DEVICE_IFACE)
            if not dev:
                continue
            addr = dev.get("Address")
            if addr is not None and addr.value.upper() == target:
                paths.append(path)

        for path in paths:
            adapter_path = path.rsplit("/", 1)[0]  # /org/bluez/hciX
            try:
                intro = await bus.introspect(BLUEZ, adapter_path)
                adapter = bus.get_proxy_object(BLUEZ, adapter_path, intro).get_interface(ADAPTER_IFACE)
                await adapter.call_remove_device(path)
                removed += 1
                _LOGGER.info("%s: removed from BlueZ on %s", address, adapter_path)
            except Exception as err:
                _LOGGER.debug("%s: RemoveDevice on %s failed: %s", address, adapter_path, err)
    finally:
        with contextlib.suppress(Exception):
            bus.disconnect()
    return removed


async def async_pair(address: str, pin: str) -> None:
    """Bond with the device using the PIN, then mark it trusted.

    Raises PairingError on failure. Safe to call again: an already-paired device
    is only (re)marked trusted.
    """
    if not (pin.isdigit() and len(pin) == 6):
        raise PairingError("PIN must be exactly 6 digits")

    with pairing_guard(address):
        await _async_pair(address, pin)


async def async_reset(
    address: str, pin: str, *, attempts: int = 6, wait: float = 15.0
) -> tuple[int, PairingError | None]:
    """Forget the device in BlueZ, then bond again: the cure for "Notify acquired".

    Holds the pairing guard across the whole sequence -- RemoveDevice, the waits
    and every re-bond attempt -- so the read coordinator stays out of the way the
    entire time (a connect during the gaps would also abort the next Pair() with
    AuthenticationCanceled). After RemoveDevice the scanner must rediscover the
    device before a bond can succeed, hence several attempts over a longer window.

    Returns how many device objects were removed and the last bond error, or
    None if the device is bonded again.
    """
    with pairing_guard(address):
        removed = await async_remove_device(address)
        last_err: PairingError | None = None
        for attempt in range(attempts):
            await asyncio.sleep(wait)
            try:
                await async_pair(address, pin)
                return removed, None
            except PairingError as err:
                last_err = err
                _LOGGER.debug(
                    "%s: re-bond attempt %d/%d failed: %s", address, attempt + 1, attempts, err
                )
        return removed, last_err


async def _async_pair(address: str, pin: str) -> None:
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        bus.export(AGENT_PATH, _build_agent(pin))

        intro = await bus.introspect(BLUEZ, ROOT)
        mgr = bus.get_proxy_object(BLUEZ, ROOT, intro).get_interface(AGENTMGR_IFACE)
        await mgr.call_register_agent(AGENT_PATH, "KeyboardDisplay")
        with contextlib.suppress(Exception):
            await mgr.call_request_default_agent(AGENT_PATH)

        try:
            path = await _find_device_path(bus, address)
            if path is None:
                raise PairingError(
                    f"{address} is not visible to the local Bluetooth adapter "
                    "(must be in range of Home Assistant's own adapter, not only a proxy)"
                )

            dev_intro = await bus.introspect(BLUEZ, path)
            dev_obj = bus.get_proxy_object(BLUEZ, path, dev_intro)
            dev = dev_obj.get_interface(DEVICE_IFACE)
            props = dev_obj.get_interface(PROPS_IFACE)

            paired = await props.call_get(DEVICE_IFACE, "Paired")
            if not paired.value:
                _LOGGER.debug("pairing: calling Pair() on %s", path)
                try:
                    await asyncio.wait_for(dev.call_pair(), PAIR_TIMEOUT)
                except TimeoutError as err:
                    raise PairingError("pairing timed out (no response from device)") from err
                except Exception as err:  # org.bluez.Error.AuthenticationFailed etc.
                    raise PairingError(f"pairing failed: {err}") from err

            await props.call_set(DEVICE_IFACE, "Trusted", Variant("b", True))
            _LOGGER.info("%s bonded and trusted", address)
        finally:
            with contextlib.suppress(Exception):
                await mgr.call_unregister_agent(AGENT_PATH)
    finally:
        with contextlib.suppress(Exception):
            bus.disconnect()
