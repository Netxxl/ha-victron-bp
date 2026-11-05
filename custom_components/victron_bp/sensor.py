"""Sensors built from the device's catalog profile (product-id driven)."""

from __future__ import annotations

import math

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .catalog import Metric
from .coordinator import BatteryProtectCoordinator, VictronBpConfigEntry
from .entity import device_info

# Unit -> (HA unit, device class). Everything else (state, mode, ...) becomes a
# plain numeric sensor with no class.
_BY_UNIT = {
    "V": ("V", SensorDeviceClass.VOLTAGE),
    "A": ("A", SensorDeviceClass.CURRENT),
    "W": ("W", SensorDeviceClass.POWER),
    "°C": ("°C", SensorDeviceClass.TEMPERATURE),
}

# VE_REG_DEVICE_STATE (0x0201) value -> option slug. This is the charge state:
# Bulk / Absorption / Float live here. Labels per language come from the enum
# "state" block in strings.json / translations/*.json. Standard VE.Direct state
# numbers; anything unmapped shows as "unknown".
_STATE_SLUGS = {
    0: "off",
    1: "low_power",
    2: "fault",
    3: "bulk",
    4: "absorption",
    5: "float",
    6: "storage",
    7: "equalize",
    8: "passthrough",
    9: "inverting",
    10: "assisting",
    11: "psu",
    0xFC: "hub1",
    255: "not_available",
}
_STATE_OPTIONS = [*dict.fromkeys(_STATE_SLUGS.values()), "unknown"]

# Register -> translation key (see strings.json / translations/*.json), so entity
# names follow Home Assistant's language. A register without a key falls back to
# the catalog's (English) label, so new ones still get a readable name.
_TRANSLATION_KEYS = {
    "VE_REG_DEVICE_STATE": "device_state",
    "VE_REG_DEVICE_OFF_REASON_2": "device_off_reason",
    "VE_REG_DC_CHANNEL1_VOLTAGE": "battery_voltage",
    "VE_REG_DC_CHANNEL1_CURRENT": "battery_current",
    "VE_REG_DC_CHANNEL1_POWER": "battery_power",
    "VE_REG_CHR_TEMPERATURE": "charger_temperature",
    "VE_REG_BAT_TEMPERATURE": "battery_temperature",
    "VE_REG_SOC": "state_of_charge",
    "VE_REG_DC_INPUT_VOLTAGE": "input_voltage",
    "VE_REG_DC_OUTPUT_VOLTAGE": "output_voltage",
    "VE_REG_DC_OUTPUT_CURRENT": "output_current",
    "VE_REG_DEVICE_MODE": "device_mode",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: VictronBpConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    # Only sensors here; writable controls (mode) are surfaced by the switch
    # platform, but they stay in the coordinator's read set so state is known.
    async_add_entities(
        VictronSensor(coordinator, m) for m in coordinator.profile.sensors
    )


class VictronSensor(CoordinatorEntity[BatteryProtectCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BatteryProtectCoordinator, metric: Metric) -> None:
        super().__init__(coordinator)
        self._metric = metric
        self._vreg_id = metric.vreg.id
        key = _TRANSLATION_KEYS.get(metric.vreg.name)
        if key is not None:
            self._attr_translation_key = key
        else:
            self._attr_name = metric.name
        self._attr_unique_id = f"{coordinator.address}_{self._vreg_id:04x}"

        # The device/charge state is an enum (Bulk/Absorption/Float/...), shown
        # as localized text instead of a raw number.
        self._is_state = metric.vreg.name == "VE_REG_DEVICE_STATE"
        if self._is_state:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = _STATE_OPTIONS
        self._attr_device_info = device_info(
            coordinator.address,
            name=coordinator.profile.title,
            model=coordinator.profile.model or coordinator.profile.family,
        )
        unit, device_class = _BY_UNIT.get(metric.unit, (None, None))
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
            self._attr_device_class = device_class
            self._attr_state_class = SensorStateClass.MEASUREMENT
            # Victron sends integers scaled by a power of ten, so the register's
            # scale is exactly the number of decimals: 100 -> 2, 10 -> 1, 1 -> 0.
            scale = metric.vreg.scale
            if scale and scale > 0:
                self._attr_suggested_display_precision = max(0, round(math.log10(scale)))

    @property
    def available(self) -> bool:
        return super().available and self._vreg_id in (self.coordinator.data or {})

    @property
    def native_value(self) -> float | int | str | None:
        value = (self.coordinator.data or {}).get(self._vreg_id)
        if self._is_state:
            return None if value is None else _STATE_SLUGS.get(int(value), "unknown")
        return value
