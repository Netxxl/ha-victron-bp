"""On/off switch for a product's writable VE_REG_MODE control.

Generic: it appears for any product whose catalog profile has a writable
DEVICE_MODE register. "Off" writes the universal value 4; "on" writes the value
the device actually reports while running (learned by the coordinator), so it
works for a BatteryProtect (on = 3) and an Orion (on = 1) alike.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .catalog import Metric
from .const import DEVICE_MODE_OFF
from .coordinator import BatteryProtectCoordinator, VictronBpConfigEntry
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: VictronBpConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        VictronModeSwitch(coordinator, m) for m in coordinator.profile.controls
    )


class VictronModeSwitch(CoordinatorEntity[BatteryProtectCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "output"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: BatteryProtectCoordinator, metric: Metric) -> None:
        super().__init__(coordinator)
        self._vreg_id = metric.vreg.id
        self._attr_unique_id = f"{coordinator.address}_{self._vreg_id:04x}_switch"
        self._attr_device_info = device_info(
            coordinator.address,
            name=coordinator.profile.title,
            model=coordinator.profile.model or coordinator.profile.family,
        )

    @property
    def available(self) -> bool:
        return super().available and self._vreg_id in (self.coordinator.data or {})

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get(self._vreg_id)
        if value is None:
            return None
        return value != DEVICE_MODE_OFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_mode(self.coordinator.mode_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_mode(DEVICE_MODE_OFF)
