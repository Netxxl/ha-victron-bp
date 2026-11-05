"""Read-only polling coordinator over one persistent GATT connection.

No value is ever written for this first test. The connection is opened once and
kept, so the device keeps streaming and the notify session stays ours.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import catalog
from .advertisement import VICTRON_MANUFACTURER_ID, product_id_from_raw
from .const import (
    CONF_KEY,
    CONF_MODE_ON,
    CONF_PIN,
    CONF_PRODUCT_ID,
    DEFAULT_MODE_ON,
    DEVICE_MODE_OFF,
    DOMAIN,
)
from .gatt import VeSmartConnection
from .pairing import async_reset

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=60)
# A reset drops the bond and re-pairs; do not loop on it if it does not help.
AUTO_RESET_COOLDOWN = 30 * 60

type VictronBpConfigEntry = ConfigEntry["BatteryProtectCoordinator"]


class BatteryProtectCoordinator(DataUpdateCoordinator[dict[int, float | int | None]]):
    def __init__(self, hass: HomeAssistant, entry: VictronBpConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.address: str = entry.data[CONF_ADDRESS]
        self.pin: str = entry.data[CONF_PIN]
        self._key = entry.data.get(CONF_KEY) or None

        # Which device is this? The product id in the advertisement selects the
        # catalog profile -- the registers to read and how to label them -- so
        # one build serves every supported Victron BLE product, not just a BatteryProtect.
        product_id = entry.data.get(CONF_PRODUCT_ID)
        if product_id is None:
            product_id = self._discover_product_id()
            if product_id is not None:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_PRODUCT_ID: product_id}
                )
        self.product_id = product_id
        self.profile = catalog.profile(product_id if product_id is not None else 0)
        _LOGGER.debug(
            "%s: product 0x%s -> %s, %d registers",
            self.address,
            f"{product_id:04X}" if product_id is not None else "????",
            self.profile.title,
            len(self.profile.read_metrics),
        )
        decoders = {m.vreg.id: m.vreg for m in self.profile.read_metrics}
        self._conn = VeSmartConnection(self._device, self.address, decoders)

        # VE_REG_MODE control, if this product has one, drives the on/off switch.
        self._mode_vreg = next(
            (m.vreg.id for m in self.profile.controls if m.vreg.name == "VE_REG_DEVICE_MODE"),
            None,
        )
        # "On" value is product-specific; start from the cache or the default and
        # relearn it whenever the device reports a running (non-off) mode.
        self.mode_on: int = entry.data.get(CONF_MODE_ON) or DEFAULT_MODE_ON
        self._last_auto_reset: float | None = None

    def _discover_product_id(self) -> int | None:
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        if info is None:
            return None
        return product_id_from_raw(info.manufacturer_data.get(VICTRON_MANUFACTURER_ID))

    def _device(self):
        return bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)

    async def _async_update_data(self) -> dict[int, float | int | None]:
        try:
            data = await self._conn.poll()
        except Exception as err:
            raise UpdateFailed(f"GATT read failed: {err}") from err
        if self._conn.notify_locked:
            self._schedule_auto_reset()
        self._learn_mode_on(data)
        return data

    def _schedule_auto_reset(self) -> None:
        """Run the reset button's cure by itself when the data channel is locked."""
        now = time.monotonic()
        if self._last_auto_reset is not None and now - self._last_auto_reset < AUTO_RESET_COOLDOWN:
            return
        self._last_auto_reset = now
        self.config_entry.async_create_background_task(
            self.hass, self._auto_reset(), f"victron_bp reset {self.address}"
        )

    async def _auto_reset(self) -> None:
        _LOGGER.warning(
            "%s: BlueZ still holds the data channel of the previous link (Notify acquired); "
            "clearing its cache and pairing again",
            self.address,
        )
        await self._conn.stop()
        removed, err = await async_reset(self.address, self.pin)
        if err is not None:
            _LOGGER.warning(
                "%s: re-pairing after the automatic reset failed: %s; "
                "next automatic try in %d min, or press the reset button",
                self.address,
                err,
                AUTO_RESET_COOLDOWN // 60,
            )
        else:
            _LOGGER.info("%s: BlueZ cache cleared (%d removed) and paired again", self.address, removed)
        await self.async_request_refresh()

    def _learn_mode_on(self, data: dict[int, float | int | None]) -> None:
        if self._mode_vreg is None:
            return
        value = data.get(self._mode_vreg)
        if isinstance(value, int) and value not in (0, DEVICE_MODE_OFF) and value != self.mode_on:
            self.mode_on = value
            entry = self.config_entry
            if entry is not None:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_MODE_ON: value}
                )

    async def async_set_device_mode(self, value: int) -> None:
        """Write VE_REG_MODE, then read back so the switch reflects the device."""
        if self._mode_vreg is None:
            raise RuntimeError("this device has no writable mode register")
        await self._conn.write_register(self._mode_vreg, bytes([value]))
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        await self._conn.stop()
        await super().async_shutdown()
