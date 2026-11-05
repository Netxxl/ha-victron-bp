"""A button that performs the one-time BLE bond with the PIN."""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import Profile
from .const import CONF_PIN
from .coordinator import VictronBpConfigEntry
from .entity import device_info
from .pairing import PairingError, async_pair, async_reset

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: VictronBpConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    profile = entry.runtime_data.profile
    async_add_entities(
        [BatteryProtectPairButton(entry, profile), BatteryProtectResetButton(entry, profile)]
    )


class BatteryProtectPairButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "pair"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, entry: VictronBpConfigEntry, profile: Profile) -> None:
        self._address: str = entry.data[CONF_ADDRESS]
        self._pin: str = entry.data[CONF_PIN]
        self._attr_unique_id = f"{self._address}_pair"
        self._attr_device_info = device_info(
            self._address, name=profile.title, model=profile.model or profile.family
        )

    async def async_press(self) -> None:
        try:
            await async_pair(self._address, self._pin)
        except PairingError as err:
            _LOGGER.warning("%s: pairing failed: %s", self._address, err)
            persistent_notification.async_create(
                self.hass,
                f"Kopplung mit {self._address} fehlgeschlagen: {err}",
                title="Victron BT Devices",
                notification_id=f"victron_bp_pair_{self._address}",
            )
            return
        _LOGGER.info("%s: paired and trusted", self._address)
        persistent_notification.async_create(
            self.hass,
            f"{self._address} wurde gekoppelt und als vertrauenswürdig markiert. "
            "Der PIN wird jetzt nicht mehr gebraucht.",
            title="Victron BT Devices",
            notification_id=f"victron_bp_pair_{self._address}",
        )


class BatteryProtectResetButton(ButtonEntity):
    """Clear BlueZ's cached GATT state for the device, then re-bond.

    This is the cure for the "Notify acquired" lock on instance 0: a stale
    notify-acquire flag that survives a plain disconnect because BlueZ keeps the
    resolved GATT table of the bonded device. RemoveDevice drops that cache; the
    device is then re-paired so the next connect starts from a clean table.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "reset"
    _attr_icon = "mdi:bluetooth-off"

    def __init__(self, entry: VictronBpConfigEntry, profile: Profile) -> None:
        self._address: str = entry.data[CONF_ADDRESS]
        self._pin: str = entry.data[CONF_PIN]
        self._attr_unique_id = f"{self._address}_reset"
        self._attr_device_info = device_info(
            self._address, name=profile.title, model=profile.model or profile.family
        )

    async def async_press(self) -> None:
        removed, last_err = await async_reset(self._address, self._pin)
        if last_err is not None:
            _LOGGER.warning("%s: re-bond after reset failed: %s", self._address, last_err)
            persistent_notification.async_create(
                self.hass,
                f"BlueZ-Cache für {self._address} geleert ({removed} Objekt(e) entfernt), "
                f"aber das erneute Koppeln kam nicht durch: {last_err}\n\n"
                "„Authentication Timeout/Canceled“ heißt meist: ein anderer BLE-Zugriff "
                "stört den Bond. Prüfe, dass konkurrierende BLE-Integrationen (z.B. bms_ble) "
                "deaktiviert sind und keine Connection-Slots blockieren, dann erneut drücken. "
                "„nicht sichtbar / Page Timeout“ = das Gerät antwortet dem HA-Adapter gerade nicht.",
                title="Victron BT Devices",
                notification_id=f"victron_bp_reset_{self._address}",
            )
            return
        _LOGGER.info("%s: BlueZ cache cleared (%d removed) and re-bonded", self._address, removed)
        persistent_notification.async_create(
            self.hass,
            f"BlueZ-Cache für {self._address} geleert ({removed} Objekt(e) entfernt) "
            "und neu gekoppelt. Beim nächsten Abruf sollte cdfd0 frei sein.",
            title="Victron BT Devices",
            notification_id=f"victron_bp_reset_{self._address}",
        )
