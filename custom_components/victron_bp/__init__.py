"""Victron BT Devices integration: product-id-driven read and output switching over BLE."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import catalog
from .coordinator import BatteryProtectCoordinator, VictronBpConfigEntry, reload_snapshot

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: VictronBpConfigEntry) -> bool:
    # file I/O must not run in the event loop; afterwards the catalog is cached
    await hass.async_add_executor_job(catalog.load)
    coordinator = BatteryProtectCoordinator(hass, entry)
    entry.runtime_data = coordinator
    # Entities first, the first read in the background: the pair button must be
    # usable while the device cannot be read yet (not bonded, out of range).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, coordinator.async_refresh(), "victron_bp first poll")
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: VictronBpConfigEntry) -> None:
    # the coordinator also stores learned values in entry.data; those need no reload
    if reload_snapshot(entry) != entry.runtime_data.snapshot:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VictronBpConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
