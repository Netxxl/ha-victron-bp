"""Shared device info for Victron BatteryProtect entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import DOMAIN


def device_info(
    address: str, *, name: str | None = None, model: str | None = None
) -> DeviceInfo:
    return DeviceInfo(
        connections={(CONNECTION_BLUETOOTH, address)},
        identifiers={(DOMAIN, address)},
        manufacturer="Victron Energy",
        model=model or "Smart device",
        name=name or "Victron",
    )
