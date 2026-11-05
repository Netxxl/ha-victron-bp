"""Config flow: discover the BatteryProtect over BLE, collect PIN and key."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .advertisement import VICTRON_MANUFACTURER_ID
from .const import CONF_KEY, CONF_PIN, DEFAULT_PIN, DOMAIN
from .vreg import PRODUCT_IDS

KEY_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
PIN_RE = re.compile(r"^\d{6}$")


def _is_battery_protect(info: BluetoothServiceInfoBleak) -> bool:
    raw = info.manufacturer_data.get(VICTRON_MANUFACTURER_ID)
    if not raw or len(raw) < 4 or raw[0] != 0x10:
        return False
    product_id = int.from_bytes(raw[1:3], "little")
    return product_id in PRODUCT_IDS


class VictronBpConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        if not _is_battery_protect(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_settings()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(self._address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_settings()

        current = self._async_current_ids()
        found = {
            info.address: f"{info.name or info.address} ({info.address})"
            for info in async_discovered_service_info(self.hass)
            if _is_battery_protect(info) and info.address not in current
        }
        schema = vol.Schema(
            {vol.Required(CONF_ADDRESS): vol.In(found) if found else str}
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = str(user_input[CONF_PIN]).strip()
            key = str(user_input.get(CONF_KEY, "")).strip()
            if not PIN_RE.match(pin):
                errors[CONF_PIN] = "invalid_pin"
            if key and not KEY_RE.match(key):
                errors[CONF_KEY] = "invalid_key"
            if not errors:
                return self.async_create_entry(
                    title=self._name or self._address or "Victron BT Devices",
                    data={CONF_ADDRESS: self._address, CONF_PIN: pin, CONF_KEY: key},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_PIN, default=DEFAULT_PIN): str,
                vol.Optional(CONF_KEY, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="settings", data_schema=schema, errors=errors,
            description_placeholders={"name": self._name or self._address or ""},
        )
