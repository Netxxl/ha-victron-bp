"""Victron BT Devices integration: product-id-driven read and output switching over BLE."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import catalog
from .coordinator import BatteryProtectCoordinator, VictronBpConfigEntry

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: VictronBpConfigEntry) -> bool:
    # file I/O must not run in the event loop; afterwards the catalog is cached
    await hass.async_add_executor_job(catalog.load)
    coordinator = BatteryProtectCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VictronBpConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
